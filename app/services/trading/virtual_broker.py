"""
가상계좌 브로커 — KIS 실시세 기반 자체 체결 시뮬레이션.

KIS 모의투자(VTS)를 쓰지 않는다. 시세·호가는 실전 KISClient에 위임하고
주문·체결·예수금만 시뮬레이션해 positions/stats 파이프라인에 실계좌와
동일한 형태로 기록한다.

체결 모델 (낙관 편향 방지 — 보수적):
- 매수 = 매도호가1(ask1), 매도 = 매수호가1(bid1) → 스프레드 비용 반영
- 호가 조회 실패(장외·동시호가 등) 시 현재가 ± 10bp fallback
- 매도 시 왕복 수수료·거래세(_COMMISSION, pnl_pct 계산과 동일 기준)를
  예수금에서 차감 → virtual_cash 곡선이 누적 pnl_pct와 정합
"""
import logging
import uuid
from decimal import Decimal

from sqlalchemy import select, update

from app.services.kis.client import (
    BalanceItem, get_kis_client, get_kis_client_from_account,
)

logger = logging.getLogger(__name__)

_SLIPPAGE = Decimal("0.001")  # 호가 조회 실패 시 현재가 대비 10bp

# 왕복 수수료+거래세 근사 — executor/_COMMISSION과 동일 값 유지
_COMMISSION: dict[str, Decimal] = {
    "KOSPI":  Decimal("0.0027"),
    "KOSDAQ": Decimal("0.0023"),
    "NAS":    Decimal("0.0050"),
}


class VirtualBroker:
    """KISClient와 동일한 주문/잔고 인터페이스. 시세 조회는 실전 클라이언트에 위임."""

    def __init__(self, account, db):
        self._account_id = account.account_id
        self._db = db
        self._market = get_kis_client(db)  # 시장 데이터 전용 (공용)
        self._fills: dict[tuple[str, str], Decimal] = {}

    def __getattr__(self, name):
        # 주문/잔고 외 메서드(get_current_price, get_stock_info 등)는 시세 클라이언트로
        return getattr(self._market, name)

    # ------------------------------------------------------------------ #
    # 체결 시뮬레이션
    # ------------------------------------------------------------------ #

    def _fill_price(self, stock_code: str, side: str) -> Decimal:
        """side "02"=매수(ask1), "01"=매도(bid1). 호가 없으면 현재가 ± 슬리피지."""
        # 실계좌 충실도: 장외/휴장에는 KIS가 시장가 주문을 거부한다. 휴장일 호가·현재가는
        # 전일 잔상이라 체결 자체가 허구가 됨 (7/17 제헌절 휴장 중 청산 체결 사례)
        if not self._market.is_market_open_now():
            raise RuntimeError(f"장 운영시간이 아닙니다 — 가상 주문 거부 ({stock_code})")
        try:
            quote = self._market.get_quote(stock_code)
            price = quote["ask1"] if side == "02" else quote["bid1"]
            if price:
                return price
        except Exception as e:
            logger.warning("Virtual quote failed for %s: %s", stock_code, e)
        current = self._market.get_current_price(stock_code)
        factor = (1 + _SLIPPAGE) if side == "02" else (1 - _SLIPPAGE)
        return (current * factor).quantize(Decimal("1"))

    def _commission_rate(self, stock_code: str) -> Decimal:
        from app.models.stock_master import StockMaster
        row = self._db.scalar(
            select(StockMaster).where(StockMaster.stock_code == stock_code)
        )
        market = (row.market if row else None) or "KOSPI"
        return _COMMISSION.get(market, _COMMISSION["KOSPI"])

    def buy_market_order(self, stock_code: str, quantity: int) -> dict:
        from app.models.user import BrokerAccount

        fill = self._fill_price(stock_code, "02")
        cost = fill * quantity
        # 잔고 체크 + 차감을 단일 UPDATE로 (동시 주문 레이스 방지)
        result = self._db.execute(
            update(BrokerAccount)
            .where(
                BrokerAccount.account_id == self._account_id,
                BrokerAccount.virtual_cash >= cost,
            )
            .values(virtual_cash=BrokerAccount.virtual_cash - cost)
        )
        if result.rowcount == 0:
            raise RuntimeError(
                f"가상계좌 예수금 부족: 필요 {cost:,.0f}원 ({stock_code} x{quantity} @ {fill:,.0f})"
            )
        self._fills[(stock_code, "02")] = fill
        logger.info("Virtual BUY %s x%d @ %s (cost=%s)", stock_code, quantity, fill, cost)
        return {"rt_cd": "0", "msg1": "가상 매수 체결", "virtual": True}

    def sell_market_order(self, stock_code: str, quantity: int) -> dict:
        from app.models.user import BrokerAccount

        fill = self._fill_price(stock_code, "01")
        # 왕복 수수료·거래세를 매도 시점에 일괄 차감 (pnl_pct 계산 convention과 일치)
        proceeds = (fill * quantity * (1 - self._commission_rate(stock_code))).quantize(Decimal("0.01"))
        self._db.execute(
            update(BrokerAccount)
            .where(BrokerAccount.account_id == self._account_id)
            .values(virtual_cash=BrokerAccount.virtual_cash + proceeds)
        )
        self._fills[(stock_code, "01")] = fill
        logger.info("Virtual SELL %s x%d @ %s (proceeds=%s)", stock_code, quantity, fill, proceeds)
        return {"rt_cd": "0", "msg1": "가상 매도 체결", "virtual": True}

    def get_today_fill_price(self, stock_code: str, side: str = "02") -> Decimal | None:
        return self._fills.get((stock_code, side))

    # ------------------------------------------------------------------ #
    # 잔고
    # ------------------------------------------------------------------ #

    def get_buyable_cash(self) -> Decimal:
        from app.models.user import BrokerAccount
        cash = self._db.scalar(
            select(BrokerAccount.virtual_cash).where(
                BrokerAccount.account_id == self._account_id
            )
        )
        return cash if cash is not None else Decimal("0")

    def get_balance(self) -> list[BalanceItem]:
        """이 가상계좌의 HOLDING 포지션을 잔고 형태로 반환.
        avg_price=entry_price라 update_entry_prices_from_balance는 no-op이 된다."""
        from app.models.position import Position, PositionStatus
        positions = self._db.scalars(
            select(Position).where(
                Position.account_id == self._account_id,
                Position.status == PositionStatus.HOLDING,
            )
        ).all()
        return [
            BalanceItem(
                stock_code=p.stock_code,
                stock_name="",
                quantity=p.quantity,
                avg_price=p.entry_price,
                current_price=p.entry_price,
                pnl_pct=Decimal("0"),
            )
            for p in positions
        ]


def get_trading_client(account):
    """계좌 타입에 맞는 브로커 반환 — VIRTUAL이면 시뮬레이터, 아니면 KISClient 싱글턴."""
    if account.account_type.value == "VIRTUAL":
        from sqlalchemy.orm import object_session
        db = object_session(account)
        if db is None:
            raise RuntimeError(f"VIRTUAL 계좌({account.account_id})가 세션에 연결돼 있지 않습니다")
        return VirtualBroker(account, db)
    return get_kis_client_from_account(account)

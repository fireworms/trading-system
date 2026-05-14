"""
자동매매 실행기.
- 매수: 추천 종목을 구독자의 invest_amount_per_pick 기준으로 시장가 매수
- 매도: 목표가/손절가/만료일 체크 후 시장가 매도
"""
import logging
from datetime import date, timedelta, datetime, timezone, time as dtime
from decimal import Decimal

from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models.position import Position, PositionStatus
from app.models.strategy import Strategy, UserStrategy
from app.models.recommendation import Recommendation, RecommendationRun
from app.services.kis.client import get_kis_client_from_account

logger = logging.getLogger(__name__)

MAX_PER_SECTOR = 2      # 섹터당 최대 매수 종목 수
RSI_OVERBOUGHT  = 70    # 이 이상이면 과매수로 스킵

# 시장별 왕복 거래비용 (수수료 + 세금)
# KOSPI : 매수 0.015% + 매도(0.015% + 거래세 0.20% + 농특세 0.04%) = 0.27%
# KOSDAQ: 매수 0.015% + 매도(0.015% + 거래세 0.20%) = 0.23%  (농특세 없음)
# NAS   : 매수 0.25% + 매도 0.25% = 0.50%
_COMMISSION: dict[str, Decimal] = {
    "KOSPI":  Decimal("0.0027"),
    "KOSDAQ": Decimal("0.0023"),
    "NAS":    Decimal("0.0050"),
}


class TradeExecutor:
    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------ #
    # 매수
    # ------------------------------------------------------------------ #

    def _check_circuit_breaker(self, user_id) -> bool:
        """직전 4건 청산이 모두 손실이면 circuit breaker 트리거. 이미 활성화됐으면 True 반환."""
        from app.core.config_store import get_config, set_config
        uid = str(user_id)
        paused_key = f"cb_paused_{uid}"
        reason_key = f"cb_reason_{uid}"

        if get_config(self.db, paused_key, "false") == "true":
            logger.warning("Circuit breaker active for user=%s: %s", uid,
                           get_config(self.db, reason_key, ""))
            return True

        recent = self.db.scalars(
            select(Position)
            .where(
                Position.user_id == user_id,
                Position.status != PositionStatus.HOLDING,
                Position.pnl_pct.isnot(None),
            )
            .order_by(Position.exit_date.desc())
            .limit(4)
        ).all()

        if len(recent) < 4:
            return False

        if all(float(p.pnl_pct) < 0 for p in recent):
            avg_pnl = sum(float(p.pnl_pct) for p in recent) / 4
            reason = f"직전 4건 청산 전부 손실 (평균 {avg_pnl:.2f}%)"
            set_config(self.db, paused_key, "true")
            set_config(self.db, reason_key, reason)
            logger.warning("Circuit breaker triggered for user=%s: %s", uid, reason)
            from app.services.telegram.notifier import notify_admins_error
            notify_admins_error("Circuit Breaker 발동", f"user={uid}\n{reason}")
            return True

        return False

    def execute_buys_for_run(self, sub: UserStrategy, run: RecommendationRun) -> None:
        """추천 run의 종목들을 구독자 계좌로 시장가 매수."""
        if not sub.is_auto_trade:
            logger.warning("Auto trade is OFF for sub=%s, skipping", sub.id)
            return

        from app.core.config_store import get_config

        # 뉴스 감시에 의한 자동매매 정지 체크
        if get_config(self.db, "news_auto_trade_paused", "false") == "true":
            reason = get_config(self.db, "news_pause_reason", "")
            logger.warning("Auto trade paused by news watch: %s", reason)
            return

        # Circuit breaker 체크 (유저별)
        if self._check_circuit_breaker(sub.user_id):
            return

        client = get_kis_client_from_account(sub.account)
        strategy = sub.strategy

        sorted_recs = sorted(run.recommendations, key=lambda r: r.rank if r.rank is not None else 999)

        try:
            buyable_cash = client.get_buyable_cash()
        except Exception as e:
            logger.error("Failed to get buyable cash: %s", e)
            return

        sector_counts: dict[str, int] = {}

        for rec in sorted_recs:
            # 확률 필터
            if rec.ai_probability is None or rec.ai_probability < strategy.min_probability:
                logger.info("Skip %s: probability %.1f < min %.1f",
                            rec.stock_code, rec.ai_probability or 0, strategy.min_probability)
                continue

            # 이미 포지션이 있으면 스킵
            existing = self.db.scalar(
                select(Position).where(
                    Position.user_id == sub.user_id,
                    Position.rec_id == rec.rec_id,
                )
            )
            if existing:
                continue

            try:
                stock_info = client.get_stock_info(rec.stock_code)
                current_price = Decimal(str(stock_info["current_price"]))

                # 수량
                quantity = int(sub.invest_amount_per_pick // current_price)
                if quantity <= 0:
                    logger.warning("invest_amount too small for %s price=%s", rec.stock_code, current_price)
                    continue

                # 남은 업사이드가 손절 리스크 이하면 스킵 (리스크/리워드 불균형)
                if rec.target_price and rec.target_price > 0:
                    remaining_upside_pct = float(
                        (rec.target_price - current_price) / current_price * 100
                    )
                    if remaining_upside_pct <= float(strategy.stop_loss_pct):
                        logger.info(
                            "Skip %s: remaining upside %.2f%% <= stop_loss %.2f%%",
                            rec.stock_code, remaining_upside_pct, float(strategy.stop_loss_pct),
                        )
                        continue

                # RSI 과매수 스킵
                rsi = stock_info.get("rsi")
                if rsi and float(rsi) > RSI_OVERBOUGHT:
                    logger.info("Skip %s: RSI=%.1f (overbought)", rec.stock_code, float(rsi))
                    continue

                # 섹터 집중도 제한 (stock_master에 없으면 KIS API 직접 조회)
                sector = (client.get_stock_basic_info(rec.stock_code) or {}).get("sector") or "unknown"
                if sector_counts.get(sector, 0) >= MAX_PER_SECTOR:
                    logger.info("Skip %s: sector '%s' already has %d positions",
                                rec.stock_code, sector, MAX_PER_SECTOR)
                    continue

                # 잔고 부족 시 중단
                order_amount = current_price * quantity
                if buyable_cash < order_amount:
                    logger.info("Stop buying: buyable_cash=%s < order_amount=%s for %s",
                                buyable_cash, order_amount, rec.stock_code)
                    break

                client.buy_market_order(rec.stock_code, quantity)
                buyable_cash -= order_amount
                sector_counts[sector] = sector_counts.get(sector, 0) + 1

                import time as _time
                _time.sleep(1)
                fill_price = client.get_today_fill_price(rec.stock_code) or current_price
                logger.info("Buy order: %s x%d @ %s (sector=%s)", rec.stock_code, quantity, fill_price, sector)

                self.db.add(Position(
                    user_id=sub.user_id,
                    strategy_id=strategy.strategy_id,
                    rec_id=rec.rec_id,
                    account_id=sub.account_id,
                    stock_code=rec.stock_code,
                    entry_price=fill_price,
                    peak_price=fill_price,
                    entry_date=date.today(),
                    quantity=quantity,
                    status=PositionStatus.HOLDING,
                ))

            except Exception as e:
                logger.error("Buy failed for %s: %s", rec.stock_code, e)

        self.db.commit()

    # ------------------------------------------------------------------ #
    # 미체결 매수 일괄 실행 (매수 전용 스케줄러 잡에서 호출)
    # ------------------------------------------------------------------ #

    def execute_pending_buys(self) -> None:
        """
        auto_trade 구독자 중 오늘 분석 run이 있는데 포지션이 없는 경우 매수 실행.
        분석 잡(08:30)과 매수 잡을 분리할 때 매수 잡에서 호출한다.
        """
        from app.core.config_store import get_config

        if get_config(self.db, "news_auto_trade_paused", "false") == "true":
            reason = get_config(self.db, "news_pause_reason", "")
            logger.warning("Auto trade paused by news watch: %s", reason)
            return

        from app.services.kis.client import get_kis_client

        # 지수 급락 시 매수 전체 보류
        try:
            market_client = get_kis_client(self.db)
            market_status = market_client.get_index_change_pct()
            kospi_chg  = market_status.get("KOSPI", 0)
            kosdaq_chg = market_status.get("KOSDAQ", 0)
            logger.info("Market status: KOSPI %+.2f%% KOSDAQ %+.2f%%", kospi_chg, kosdaq_chg)
            if kospi_chg <= -2.0 or kosdaq_chg <= -2.0:
                logger.warning("Market down — skipping all buys (KOSPI=%+.2f%% KOSDAQ=%+.2f%%)",
                               kospi_chg, kosdaq_chg)
                return
        except Exception as e:
            logger.warning("Failed to get index status: %s", e)

        subs = self.db.scalars(
            select(UserStrategy).where(
                UserStrategy.is_active == True,
                UserStrategy.is_auto_trade == True,
            )
        ).all()

        bearish_keywords = ["하락", "위험", "침체", "약세", "bear", "bearish"]

        for sub in subs:
            strategy: Strategy = sub.strategy

            # 가장 최근 run (run_interval_days * 2일 이내)
            cutoff = date.today() - timedelta(days=strategy.run_interval_days * 2)
            run = self.db.scalar(
                select(RecommendationRun)
                .where(
                    RecommendationRun.strategy_id == strategy.strategy_id,
                    RecommendationRun.run_date >= cutoff,
                )
                .order_by(RecommendationRun.run_date.desc())
                .limit(1)
            )
            if run is None:
                continue

            # 이미 이 run에서 매수한 포지션이 있으면 스킵
            already_bought = self.db.scalar(
                select(Position)
                .join(Recommendation, Position.rec_id == Recommendation.rec_id)
                .where(
                    Recommendation.run_id == run.run_id,
                    Position.user_id == sub.user_id,
                )
                .limit(1)
            )
            if already_bought:
                logger.info("Sub=%s run=%s already has positions, skipping", sub.id, run.run_id)
                continue

            # 하락장 판단
            market_theme = ""
            if run.raw_response and isinstance(run.raw_response.get("macro"), dict):
                market_theme = (run.raw_response["macro"].get("market_theme") or "").lower()
            is_bearish = any(kw in market_theme for kw in bearish_keywords)

            try:
                if is_bearish:
                    original = sub.invest_amount_per_pick
                    sub.invest_amount_per_pick = (original / 2).quantize(Decimal("1"))
                    logger.info("Bearish market — invest halved for sub=%s", sub.id)
                    self.execute_buys_for_run(sub, run)
                    sub.invest_amount_per_pick = original
                else:
                    self.execute_buys_for_run(sub, run)
            except Exception as e:
                logger.error("execute_pending_buys failed for sub=%s: %s", sub.id, e)

        # 체결 직후 실 체결가로 entry_price 보정
        self.update_entry_prices_from_balance()

    # ------------------------------------------------------------------ #
    # 체결가 업데이트 (09:05 첫 모니터 시 실 체결가로 보정)
    # ------------------------------------------------------------------ #

    def update_entry_prices_from_balance(self) -> None:
        """
        오늘 생성된 HOLDING 포지션의 entry_price를 KIS 잔고의 실 체결가(avg_price)로 업데이트.
        동시호가(08:30) 주문은 09:00에 체결되므로 09:05 모니터 시 호출.
        """
        today_positions = self.db.scalars(
            select(Position).where(
                Position.status == PositionStatus.HOLDING,
                Position.entry_date == date.today(),
            )
        ).all()

        if not today_positions:
            return

        # account_id별로 그룹화해서 잔고 1회씩만 조회
        from collections import defaultdict
        from app.models.strategy import UserStrategy
        by_account: dict = defaultdict(list)
        for pos in today_positions:
            by_account[pos.account_id].append(pos)

        from app.models.user import BrokerAccount
        for account_id, positions in by_account.items():
            account = self.db.get(BrokerAccount, account_id)
            if not account:
                continue
            try:
                client = get_kis_client_from_account(account)
                balance_items = client.get_balance()
                fill_map = {item.stock_code: item.avg_price for item in balance_items}

                for pos in positions:
                    fill_price = fill_map.get(pos.stock_code)
                    if fill_price and fill_price > 0 and fill_price != pos.entry_price:
                        logger.info(
                            "Update entry_price %s: %s → %s (actual fill)",
                            pos.stock_code, pos.entry_price, fill_price,
                        )
                        pos.entry_price = fill_price
                        # 손절가 기준점을 실 체결가로 초기화
                        pos.peak_price = fill_price
            except Exception as e:
                logger.error("Failed to update fill prices for account=%s: %s", account_id, e)

        self.db.commit()

    # ------------------------------------------------------------------ #
    # 포지션 모니터링 (매일 장중)
    # ------------------------------------------------------------------ #

    def monitor_positions(self) -> None:
        # 오늘 생성 포지션은 실 체결가로 entry_price 보정
        self.update_entry_prices_from_balance()

        positions = self.db.scalars(
            select(Position).where(Position.status == PositionStatus.HOLDING)
        ).all()

        logger.info("Monitoring %d positions", len(positions))

        for pos in positions:
            try:
                self._check_position(pos)
            except Exception as e:
                logger.error("Monitor error for position=%s: %s", pos.position_id, e)

        self.db.commit()

    def _check_position(self, pos: Position) -> None:
        rec = pos.recommendation
        strategy = pos.strategy
        client = get_kis_client_from_account(pos.account)

        current_price = client.get_current_price(pos.stock_code)
        today = date.today()
        hold_days_elapsed = (today - pos.entry_date).days

        # peak_price 갱신
        if pos.peak_price is None or current_price > pos.peak_price:
            pos.peak_price = current_price

        # 만료 체크
        if hold_days_elapsed >= strategy.hold_days:
            self._close_position(pos, current_price, PositionStatus.EXPIRED, client)
            return

        # Time-based Stop: 5일 이후에도 손실 중이면 조기 청산
        if hold_days_elapsed >= 5:
            if current_price < pos.entry_price:
                logger.info("Time-based stop for %s: day=%d, pnl=%.2f%%",
                            pos.stock_code, hold_days_elapsed,
                            float((current_price - pos.entry_price) / pos.entry_price * 100))
                self._close_position(pos, current_price, PositionStatus.EXPIRED, client)
                return

        # 목표가: rec 있으면 rec.target_price, 없으면 strategy × entry_price (수동매수 포함)
        target_price = (
            rec.target_price if rec and rec.target_price
            else (pos.entry_price * (1 + strategy.target_pct / 100)).quantize(Decimal("1"))
            if strategy and pos.entry_price else None
        )

        # 목표가 도달
        if target_price and current_price >= target_price:
            if getattr(strategy, "use_trailing_stop", False):
                # 트레일링 옵션 ON: 목표가 최초 도달 시 트레일링 모드 전환
                if pos.target_hit_at is None:
                    pos.target_hit_at = datetime.now(timezone.utc)
                    pos.target_hit_peak = pos.peak_price
                    logger.info("Target hit %s @ %s — trailing mode activated", pos.stock_code, current_price)
                    return
                # 이미 트레일링 중 → 아래 손절 로직에서 처리
            else:
                # 트레일링 옵션 OFF (기본): 즉시 익절 (AI thesis 완료)
                logger.info("Target hit %s @ %s — closing immediately", pos.stock_code, current_price)
                self._close_position(pos, current_price, PositionStatus.TARGET_HIT, client)
                return

        # 손절
        if pos.target_hit_at is not None:
            # 트레일링 모드: peak 기준 trailing stop
            trailing_stop = pos.peak_price * (1 - strategy.stop_loss_pct / 100)
            if current_price <= trailing_stop:
                logger.info("Trailing stop %s: peak=%s current=%s stop=%s",
                            pos.stock_code, pos.peak_price, current_price, trailing_stop)
                self._close_position(pos, current_price, PositionStatus.STOP_LOSS, client)
                return
        else:
            # 일반 모드: entry 기준 고정 손절
            fixed_stop = pos.entry_price * (1 - strategy.stop_loss_pct / 100)
            if current_price <= fixed_stop:
                logger.info("Fixed stop %s: entry=%s current=%s stop=%s",
                            pos.stock_code, pos.entry_price, current_price, fixed_stop)
                self._close_position(pos, current_price, PositionStatus.STOP_LOSS, client)
                return

    @staticmethod
    def _trading_days_since(start: date, end: date) -> int:
        if start >= end:
            return 0
        count = 0
        cur = start + timedelta(days=1)
        while cur <= end:
            if cur.weekday() < 5:
                count += 1
            cur += timedelta(days=1)
        return count

    def _get_commission_rate(self, stock_code: str) -> Decimal:
        """stock_master에서 시장 조회 후 왕복 수수료율 반환."""
        from app.models.stock_master import StockMaster
        row = self.db.scalar(
            select(StockMaster).where(StockMaster.stock_code == stock_code)
        )
        market = (row.market if row else None) or "KOSPI"
        return _COMMISSION.get(market, _COMMISSION["KOSPI"])

    def _close_position(
        self,
        pos: Position,
        current_price: Decimal,
        new_status: PositionStatus,
        client,
    ) -> None:
        try:
            client.sell_market_order(pos.stock_code, pos.quantity)
        except Exception as e:
            logger.error("Sell order failed for %s: %s", pos.stock_code, e)
            return

        import time as _time
        _time.sleep(1)
        exit_price = client.get_today_fill_price(pos.stock_code, side="01") or current_price

        commission = self._get_commission_rate(pos.stock_code)
        pnl = (exit_price - pos.entry_price) / pos.entry_price * 100 - commission * 100

        pos.exit_price = exit_price
        pos.exit_date = date.today()
        pos.status = new_status
        pos.pnl_pct = Decimal(str(round(float(pnl), 4)))

        logger.info(
            "Closed position %s %s: entry=%s exit=%s pnl=%.2f%%",
            pos.stock_code, new_status.value,
            pos.entry_price, exit_price, float(pnl),
        )

        from app.services.telegram.notifier import get_notifier
        from app.models.user import User
        notifier = get_notifier()
        if notifier:
            user = self.db.get(User, pos.user_id)
            if user and user.telegram_chat_id:
                notifier.notify_position_closed(
                    chat_id=user.telegram_chat_id,
                    stock_code=pos.stock_code,
                    stock_name=pos.recommendation.stock_name if pos.recommendation else pos.stock_code,
                    status=new_status.value,
                    entry_price=pos.entry_price,
                    exit_price=exit_price,
                    pnl_pct=pos.pnl_pct,
                    strategy_name=pos.strategy.name if pos.strategy else "",
                )

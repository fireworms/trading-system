"""
자동매매 실행기.
- 매수: 추천 종목을 구독자의 invest_amount_per_pick 기준으로 시장가 매수
- 매도: 목표가/손절가/만료일 체크 후 시장가 매도
"""
import logging
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models.position import Position, PositionStatus
from app.models.strategy import UserStrategy
from app.models.recommendation import RecommendationRun, Recommendation
from app.services.kis.client import get_kis_client_from_account

logger = logging.getLogger(__name__)


class TradeExecutor:
    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------ #
    # 매수
    # ------------------------------------------------------------------ #

    def execute_buys_for_run(self, sub: UserStrategy, run: RecommendationRun) -> None:
        """
        추천 run의 종목들을 구독자 계좌로 시장가 매수.
        is_auto_trade 반드시 확인.
        """
        if not sub.is_auto_trade:
            logger.warning("Auto trade is OFF for sub=%s, skipping", sub.id)
            return

        client = get_kis_client_from_account(sub.account)
        strategy = sub.strategy

        for rec in run.recommendations:
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
                current_price = client.get_current_price(rec.stock_code)
                quantity = int(sub.invest_amount_per_pick // current_price)
                if quantity <= 0:
                    logger.warning("invest_amount too small for %s price=%s", rec.stock_code, current_price)
                    continue

                order_result = client.buy_market_order(rec.stock_code, quantity)
                logger.info("Buy order: %s x%d, result=%s", rec.stock_code, quantity, order_result)

                position = Position(
                    user_id=sub.user_id,
                    strategy_id=strategy.strategy_id,
                    rec_id=rec.rec_id,
                    account_id=sub.account_id,
                    stock_code=rec.stock_code,
                    entry_price=current_price,
                    entry_date=date.today(),
                    quantity=quantity,
                    status=PositionStatus.HOLDING,
                )
                self.db.add(position)

            except Exception as e:
                logger.error("Buy failed for %s: %s", rec.stock_code, e)

        self.db.commit()

    # ------------------------------------------------------------------ #
    # 포지션 모니터링 (매일 장중)
    # ------------------------------------------------------------------ #

    def monitor_positions(self) -> None:
        """
        보유 포지션 전체 체크:
        - 목표가 도달 → TARGET_HIT 매도
        - 손절가 도달 → STOP_LOSS 매도
        - 보유기간 만료 → EXPIRED 매도
        """
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

        # 만료 체크
        if hold_days_elapsed >= strategy.hold_days:
            self._close_position(pos, current_price, PositionStatus.EXPIRED, client)
            return

        # 목표가 도달
        if rec.target_price and current_price >= rec.target_price:
            self._close_position(pos, current_price, PositionStatus.TARGET_HIT, client)
            return

        # 손절가 도달
        if rec.stop_loss_price and current_price <= rec.stop_loss_price:
            self._close_position(pos, current_price, PositionStatus.STOP_LOSS, client)
            return

    def _close_position(
        self,
        pos: Position,
        exit_price: Decimal,
        new_status: PositionStatus,
        client,
    ) -> None:
        try:
            client.sell_market_order(pos.stock_code, pos.quantity)
        except Exception as e:
            logger.error("Sell order failed for %s: %s", pos.stock_code, e)
            return

        pnl = (exit_price - pos.entry_price) / pos.entry_price * 100

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

"""
자동매매 실행기.
- 매수: 추천 종목을 구독자의 invest_amount_per_pick 기준으로 시장가 매수
- 매도: 목표가/손절가/만료일 체크 후 시장가 매도
"""
import logging
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models.position import Position, PositionStatus
from app.models.strategy import UserStrategy
from app.models.recommendation import RecommendationRun
from app.services.kis.client import get_kis_client_from_account

logger = logging.getLogger(__name__)

MAX_PER_SECTOR = 2      # 섹터당 최대 매수 종목 수
RSI_OVERBOUGHT  = 70    # 이 이상이면 과매수로 스킵


class TradeExecutor:
    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------ #
    # 매수
    # ------------------------------------------------------------------ #

    def execute_buys_for_run(self, sub: UserStrategy, run: RecommendationRun) -> None:
        """
        추천 run의 종목들을 구독자 계좌로 시장가 매수.
        - rank 오름차순 처리
        - 잔고 부족 시 중단
        - RSI 과매수 / 목표가 초과 / 섹터 집중 종목 스킵
        """
        if not sub.is_auto_trade:
            logger.warning("Auto trade is OFF for sub=%s, skipping", sub.id)
            return

        # 뉴스 감시에 의한 자동매매 정지 체크
        from app.core.config_store import get_config
        if get_config(self.db, "news_auto_trade_paused", "false") == "true":
            reason = get_config(self.db, "news_pause_reason", "")
            logger.warning("Auto trade paused by news watch: %s", reason)
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

                # 현재가가 AI 목표가 이상이면 스킵
                if rec.target_price and current_price >= rec.target_price:
                    logger.info("Skip %s: current_price=%s >= target_price=%s",
                                rec.stock_code, current_price, rec.target_price)
                    continue

                # RSI 과매수 스킵
                rsi = stock_info.get("rsi")
                if rsi and float(rsi) > RSI_OVERBOUGHT:
                    logger.info("Skip %s: RSI=%.1f (overbought)", rec.stock_code, float(rsi))
                    continue

                # 섹터 집중도 제한
                sector = stock_info.get("sector") or "unknown"
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
                logger.info("Buy order: %s x%d (sector=%s)", rec.stock_code, quantity, sector)

                self.db.add(Position(
                    user_id=sub.user_id,
                    strategy_id=strategy.strategy_id,
                    rec_id=rec.rec_id,
                    account_id=sub.account_id,
                    stock_code=rec.stock_code,
                    entry_price=current_price,
                    entry_date=date.today(),
                    quantity=quantity,
                    status=PositionStatus.HOLDING,
                ))

            except Exception as e:
                logger.error("Buy failed for %s: %s", rec.stock_code, e)

        self.db.commit()

    # ------------------------------------------------------------------ #
    # 포지션 모니터링 (매일 장중)
    # ------------------------------------------------------------------ #

    def monitor_positions(self) -> None:
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

        # 목표가 도달 (AI 분석 기준 절대 가격)
        if rec.target_price and current_price >= rec.target_price:
            self._close_position(pos, current_price, PositionStatus.TARGET_HIT, client)
            return

        # 트레일링 스탑: peak 대비 -stop_loss_pct% 이탈 시 청산
        if pos.peak_price:
            trailing_stop = pos.peak_price * (1 - strategy.stop_loss_pct / 100)
            if current_price <= trailing_stop:
                logger.info("Trailing stop for %s: peak=%s current=%s stop=%s",
                            pos.stock_code, pos.peak_price, current_price, trailing_stop)
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

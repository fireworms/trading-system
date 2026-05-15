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

# 크로스 시그널 보너스 상한 (어떤 경우도 이 이상 올리지 않음)
_CROSS_SIGNAL_MAX_BONUS = Decimal("10")


def _build_cross_signal(db) -> dict[str, float]:
    """
    오늘 실행된 모든 전략의 추천을 종목별로 집계해 다양성 점수를 반환한다.
    같은 (candidate_filter, candidate_market) 조합의 전략은 0.5점,
    다른 조합은 1.0점으로 가중 — 전략이 독립적일수록 높은 점수.
    단일 전략 종목은 0.0점.
    """
    from collections import defaultdict
    from datetime import date

    today_runs = db.scalars(
        select(RecommendationRun).where(
            RecommendationRun.run_date == date.today(),
            RecommendationRun.stage4_skipped == False,
        )
    ).all()

    # stock_code → {(filter, market): count}
    combo_map: dict[str, dict[tuple, int]] = defaultdict(lambda: defaultdict(int))
    for run in today_runs:
        strategy = run.strategy
        combo = (
            getattr(strategy, "candidate_filter", "mixed"),
            getattr(strategy, "candidate_market", "ALL"),
        )
        for rec in run.recommendations:
            combo_map[rec.stock_code][combo] += 1

    result: dict[str, float] = {}
    for code, combos in combo_map.items():
        if sum(combos.values()) <= 1:
            result[code] = 0.0
            continue
        # 고유 콤보 수로 점수 계산 (다양한 필터 조합일수록 높음)
        unique_combos = len(combos)
        total_count = sum(combos.values())
        score = (unique_combos * 1.0) + (total_count - unique_combos) * 0.5
        result[code] = round(score, 2)

    return result


def _cross_signal_bonus(stock_code: str, cross_signal: dict[str, float] | None) -> float:
    """다양성 점수를 확률 보너스(%)로 변환. 상한 10%."""
    if not cross_signal:
        return 0.0
    score = cross_signal.get(stock_code, 0.0)
    if score <= 0:
        return 0.0
    # score 1.0 → +7%, 0.5 → +3%, 2.0 이상 → +10% (상한)
    bonus = min(score * 7.0, float(_CROSS_SIGNAL_MAX_BONUS))
    return round(bonus, 1)

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

    def execute_buys_for_run(
        self,
        sub: UserStrategy,
        run: RecommendationRun,
        cross_signal: dict[str, float] | None = None,
    ) -> None:
        """추천 run의 종목들을 구독자 계좌로 시장가 매수."""
        if not sub.is_auto_trade:
            logger.warning("Auto trade is OFF for sub=%s, skipping", sub.id)
            return

        from app.core.config_store import get_config

        # 뉴스 감시 / 모닝 게이트에 의한 자동매매 정지 체크
        if get_config(self.db, "news_auto_trade_paused", "false") == "true":
            reason = get_config(self.db, "news_pause_reason", "")
            logger.warning("Auto trade paused by news watch: %s", reason)
            return
        if get_config(self.db, "morning_gate_paused", "false") == "true":
            reason = get_config(self.db, "morning_gate_reason", "")
            logger.warning("Auto trade paused by morning gate: %s", reason)
            return

        # Circuit breaker 체크 (유저별)
        if self._check_circuit_breaker(sub.user_id):
            return

        client = get_kis_client_from_account(sub.account)
        strategy = sub.strategy

        # 크로스 시그널 보너스 적용 후 정렬
        # 보너스 우선, 동점이면 rank 순
        def _sort_key(r):
            bonus = _cross_signal_bonus(r.stock_code, cross_signal)
            eff_prob = float(r.ai_probability or 0) + bonus
            return (-eff_prob, r.rank if r.rank is not None else 999)

        sorted_recs = sorted(run.recommendations, key=_sort_key)

        try:
            buyable_cash = client.get_buyable_cash()
        except Exception as e:
            logger.error("Failed to get buyable cash: %s", e)
            return

        sector_counts: dict[str, int] = {}

        for rec in sorted_recs:
            # 크로스 시그널 보너스 포함 유효 확률
            bonus = _cross_signal_bonus(rec.stock_code, cross_signal)
            effective_prob = (rec.ai_probability or Decimal("0")) + Decimal(str(bonus))
            if bonus > 0:
                logger.info("Cross signal bonus +%.1f%% for %s → effective_prob=%.1f",
                            bonus, rec.stock_code, float(effective_prob))

            # 확률 필터 (보너스 포함 유효 확률 기준)
            if effective_prob < strategy.min_probability:
                logger.info("Skip %s: effective_prob %.1f < min %.1f",
                            rec.stock_code, float(effective_prob), strategy.min_probability)
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

        # 새로 매수된 포지션을 실시간 모니터에 반영
        from app.services.trading.realtime_monitor import get_monitor
        get_monitor().load_all()

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
        if get_config(self.db, "morning_gate_paused", "false") == "true":
            reason = get_config(self.db, "morning_gate_reason", "")
            logger.warning("Auto trade paused by morning gate: %s", reason)
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

        # 크로스 시그널 맵 사전 계산
        # 오늘 실행된 모든 전략의 추천을 모아 종목별 다양성 점수 집계
        cross_signal = _build_cross_signal(self.db)
        if cross_signal:
            logger.info("Cross signal map: %s", {k: v for k, v in cross_signal.items() if v > 0})

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
                    self.execute_buys_for_run(sub, run, cross_signal=cross_signal)
                    sub.invest_amount_per_pick = original
                else:
                    self.execute_buys_for_run(sub, run, cross_signal=cross_signal)
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

        # trailing 여부: 포지션 override 우선, 없으면 전략 기본값
        use_trailing = (
            pos.trailing_stop_override
            if pos.trailing_stop_override is not None
            else getattr(strategy, "use_trailing_stop", False)
        )

        # 목표가 도달
        if target_price and current_price >= target_price:
            if use_trailing:
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
        if pos.target_hit_at is not None and use_trailing:
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

        # 실시간 모니터에서 제거 (이미 없으면 무시)
        from app.services.trading.realtime_monitor import get_monitor
        get_monitor().remove(str(pos.position_id), pos.stock_code)

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

    # ------------------------------------------------------------------ #
    # 뉴스 긴급 조치
    # ------------------------------------------------------------------ #

    def emergency_close_all_positions(self, reason: str) -> int:
        """CRITICAL + KOSPI -2% 동시 감지 시 모든 HOLDING 포지션 즉시 청산."""
        positions = self.db.scalars(
            select(Position).where(Position.status == PositionStatus.HOLDING)
        ).all()

        closed = 0
        for pos in positions:
            try:
                client = get_kis_client_from_account(pos.account)
                current_price = client.get_current_price(pos.stock_code)
                self._close_position(pos, current_price, PositionStatus.MANUAL_EXIT, client)
                closed += 1
            except Exception as e:
                logger.error("Emergency close failed for %s: %s", pos.stock_code, e)

        self.db.commit()
        logger.warning("Emergency closed %d positions: %s", closed, reason)
        return closed

    def tighten_stop_losses(self, reason: str) -> int:
        """WARNING + KOSPI -1% 동시 감지 시 수익 포지션을 현재가 기준 트레일링으로 전환.
        손실 중인 포지션은 기존 고정 손절선 유지.
        """
        from app.services.trading.realtime_monitor import get_monitor

        positions = self.db.scalars(
            select(Position).where(Position.status == PositionStatus.HOLDING)
        ).all()

        monitor = get_monitor()
        tightened = 0
        now = datetime.now(timezone.utc)

        for pos in positions:
            try:
                client = get_kis_client_from_account(pos.account)
                current_price = client.get_current_price(pos.stock_code)

                if current_price <= pos.entry_price:
                    continue  # 손실 포지션: 기존 고정 손절선이 더 보수적

                pos.peak_price = current_price
                if pos.target_hit_at is None:
                    pos.target_hit_at = now
                    pos.target_hit_peak = current_price
                pos.trailing_stop_override = True

                monitor.force_trailing(str(pos.position_id), current_price)

                stop = current_price * (1 - pos.strategy.stop_loss_pct / 100)
                logger.info("Tightened stop %s: current=%s new_stop=%.0f", pos.stock_code, current_price, float(stop))
                tightened += 1
            except Exception as e:
                logger.error("Tighten failed for %s: %s", pos.stock_code, e)

        self.db.commit()
        logger.warning("Tightened stops for %d positions: %s", tightened, reason)
        return tightened

"""
실시간 포지션 모니터.

KIS WebSocket 가격 콜백에서 손절가/목표가 이탈을 즉시 감지해 청산한다.
10분 폴링(monitor_positions)은 만료·time-based stop 및 fallback 역할로 그대로 유지된다.

흐름:
  1. 서버 시작 시 load_all() → HOLDING 포지션 전부 인메모리 등록 + KIS 구독
  2. 매수 후 load_all() 재호출 → 새 포지션 반영
  3. on_price() → bid_price 기준으로 조건 체크 → 조건 충족 시 asyncio.create_task로 비동기 청산
  4. 청산 완료 후 remove() → 해당 코드를 KIS에서도 구독 해제 (프론트도 안 보면)
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

logger = logging.getLogger(__name__)


@dataclass
class PositionWatch:
    position_id: str
    account_id: str
    stock_code: str
    user_id: str
    quantity: int
    entry_price: Decimal
    peak_price: Decimal
    target_price: Decimal | None
    stop_loss_pct: Decimal
    use_trailing: bool
    target_hit_at: datetime | None
    target_hit_peak: Decimal | None


class RealtimePositionMonitor:
    def __init__(self) -> None:
        # code → {position_id → PositionWatch}
        self._by_code: dict[str, dict[str, PositionWatch]] = {}
        # 청산 진행 중인 position_id (중복 트리거 방지)
        self._closing: set[str] = set()

    # ------------------------------------------------------------------ #
    # 등록 / 해제
    # ------------------------------------------------------------------ #

    def add(self, watch: PositionWatch) -> None:
        self._by_code.setdefault(watch.stock_code, {})[watch.position_id] = watch

    def remove(self, position_id: str, stock_code: str) -> None:
        bucket = self._by_code.get(stock_code, {})
        bucket.pop(position_id, None)
        if not bucket:
            self._by_code.pop(stock_code, None)
        self._closing.discard(position_id)

    def is_watched(self, stock_code: str) -> bool:
        return bool(self._by_code.get(stock_code))

    def watched_codes(self) -> set[str]:
        return set(self._by_code.keys())

    # ------------------------------------------------------------------ #
    # DB 로드 (서버 시작 / 매수 완료 후 재호출)
    # ------------------------------------------------------------------ #

    def load_all(self) -> None:
        """DB의 모든 HOLDING 포지션을 인메모리에 동기화하고 KIS 코드를 구독한다."""
        from app.core.database import SessionLocal
        from app.models.position import Position, PositionStatus
        from sqlalchemy import select

        try:
            with SessionLocal() as db:
                positions = db.scalars(
                    select(Position).where(Position.status == PositionStatus.HOLDING)
                ).all()

                seen_ids: set[str] = set()
                new_codes: set[str] = set()

                for pos in positions:
                    watch = _make_watch(pos)
                    if watch:
                        self.add(watch)
                        seen_ids.add(watch.position_id)
                        new_codes.add(pos.stock_code)

                # HOLDING이 아닌 포지션은 워치에서 제거
                for code, bucket in list(self._by_code.items()):
                    for pid in list(bucket.keys()):
                        if pid not in seen_ids:
                            self.remove(pid, code)

            # 새로 등록된 코드 KIS 구독
            _subscribe_codes(new_codes)
            logger.info("RT monitor synced: %d positions, codes=%s",
                        len(seen_ids), new_codes or "(none)")
        except Exception as e:
            logger.error("RT monitor load_all failed: %s", e)

    # ------------------------------------------------------------------ #
    # 가격 콜백 (async — KIS WS 루프에서 직접 호출)
    # ------------------------------------------------------------------ #

    async def on_price(self, code: str, price_data: dict) -> None:
        bucket = self._by_code.get(code)
        if not bucket:
            return

        # 손절 판단은 bid_price 기준 (시장가 매도 시 실체결 기준가)
        raw = price_data.get("bid_price") or price_data.get("current_price", 0)
        current_price = Decimal(str(raw))
        if current_price <= 0:
            return

        for position_id, watch in list(bucket.items()):
            if position_id in self._closing:
                continue

            # peak 갱신 (인메모리만, DB는 10분 폴링이 처리)
            if current_price > watch.peak_price:
                watch.peak_price = current_price

            # 목표가 도달 + 트레일링 모드 진입
            if watch.target_price and current_price >= watch.target_price:
                if watch.use_trailing and watch.target_hit_at is None:
                    watch.target_hit_at = datetime.now(timezone.utc)
                    watch.target_hit_peak = watch.peak_price
                    asyncio.create_task(_write_target_hit_async(
                        position_id, watch.target_hit_at, watch.target_hit_peak
                    ))
                    logger.info("RT: trailing mode activated %s @ %s", code, current_price)
                    continue  # 이번 틱은 청산 안 함

            close_reason = self._should_close(watch, current_price)
            if close_reason:
                self._closing.add(position_id)
                asyncio.create_task(
                    _close_position_async(watch, current_price, close_reason)
                )

    def _should_close(self, watch: PositionWatch, current_price: Decimal) -> str | None:
        if watch.target_hit_at is not None and watch.use_trailing:
            trailing_stop = watch.peak_price * (1 - watch.stop_loss_pct / 100)
            if current_price <= trailing_stop:
                return "STOP_LOSS"
        else:
            fixed_stop = watch.entry_price * (1 - watch.stop_loss_pct / 100)
            if current_price <= fixed_stop:
                return "STOP_LOSS"

        if watch.target_price and current_price >= watch.target_price and not watch.use_trailing:
            return "TARGET_HIT"

        return None


# ------------------------------------------------------------------ #
# 싱글턴
# ------------------------------------------------------------------ #

_monitor: RealtimePositionMonitor | None = None


def get_monitor() -> RealtimePositionMonitor:
    global _monitor
    if _monitor is None:
        _monitor = RealtimePositionMonitor()
    return _monitor


# ------------------------------------------------------------------ #
# 내부 헬퍼
# ------------------------------------------------------------------ #

def _make_watch(pos) -> PositionWatch | None:
    """Position ORM 객체를 PositionWatch로 변환."""
    try:
        strategy = pos.strategy
        if not strategy:
            return None
        rec = pos.recommendation

        target_price = (
            rec.target_price if rec and rec.target_price
            else (pos.entry_price * (1 + strategy.target_pct / 100)).quantize(Decimal("1"))
            if pos.entry_price else None
        )
        use_trailing = (
            pos.trailing_stop_override
            if pos.trailing_stop_override is not None
            else getattr(strategy, "use_trailing_stop", False)
        )

        return PositionWatch(
            position_id=str(pos.position_id),
            account_id=str(pos.account_id),
            stock_code=pos.stock_code,
            user_id=str(pos.user_id),
            quantity=pos.quantity,
            entry_price=pos.entry_price,
            peak_price=pos.peak_price or pos.entry_price,
            target_price=target_price,
            stop_loss_pct=Decimal(str(strategy.stop_loss_pct)),
            use_trailing=bool(use_trailing),
            target_hit_at=pos.target_hit_at,
            target_hit_peak=pos.target_hit_peak,
        )
    except Exception as e:
        logger.error("_make_watch failed pos=%s: %s",
                     getattr(pos, "position_id", "?"), e)
        return None


def _subscribe_codes(codes: set[str]) -> None:
    """동기 컨텍스트에서 KIS WebSocket 코드 구독 요청."""
    from app.services.kis.realtime import get_realtime_client
    from app.core.loop import get_loop

    rt = get_realtime_client()
    if not rt or not codes:
        return
    loop = get_loop()
    for code in codes:
        if loop and loop.is_running():
            asyncio.run_coroutine_threadsafe(rt.subscribe(code), loop)
        else:
            rt._subscribed.add(code)


async def _write_target_hit_async(
    position_id: str,
    target_hit_at: datetime,
    target_hit_peak: Decimal,
) -> None:
    """트레일링 모드 진입 기록을 백그라운드로 DB에 저장."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None, _write_target_hit_sync, position_id, target_hit_at, target_hit_peak
    )


def _write_target_hit_sync(
    position_id: str,
    target_hit_at: datetime,
    target_hit_peak: Decimal,
) -> None:
    from app.core.database import SessionLocal
    from app.models.position import Position

    try:
        with SessionLocal() as db:
            pos = db.get(Position, uuid.UUID(position_id))
            if pos and pos.target_hit_at is None:
                pos.target_hit_at = target_hit_at
                pos.target_hit_peak = target_hit_peak
                db.commit()
    except Exception as e:
        logger.error("write_target_hit failed pos=%s: %s", position_id, e)


async def _close_position_async(
    watch: PositionWatch,
    current_price: Decimal,
    reason: str,
) -> None:
    """청산 트리거 — 별도 스레드에서 동기 청산 로직 실행."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None, _close_position_sync, watch, current_price, reason
    )


def _close_position_sync(
    watch: PositionWatch,
    current_price: Decimal,
    reason: str,
) -> None:
    from app.core.database import SessionLocal
    from app.models.position import Position, PositionStatus
    from app.services.kis.client import get_kis_client_from_account
    from app.services.trading.executor import TradeExecutor

    monitor = get_monitor()
    try:
        with SessionLocal() as db:
            pos = db.get(Position, uuid.UUID(watch.position_id))
            if not pos or pos.status != PositionStatus.HOLDING:
                # 10분 폴링이 먼저 청산했거나 이미 처리됨
                logger.info("RT close skipped pos=%s status=%s",
                            watch.position_id, pos.status if pos else "NOT FOUND")
                return

            new_status = (
                PositionStatus.TARGET_HIT
                if reason == "TARGET_HIT"
                else PositionStatus.STOP_LOSS
            )
            executor = TradeExecutor(db)
            client = get_kis_client_from_account(pos.account)
            executor._close_position(pos, current_price, new_status, client)
            db.commit()

        monitor.remove(watch.position_id, watch.stock_code)
        logger.info("RT closed %s %s @ %s reason=%s",
                    watch.stock_code, watch.position_id, current_price, reason)
    except Exception as e:
        logger.error("RT close_position_sync failed pos=%s: %s", watch.position_id, e)
    finally:
        monitor._closing.discard(watch.position_id)

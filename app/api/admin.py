"""
관리자 전용 수동 실행 엔드포인트.
"""
import uuid
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.models.strategy import Strategy
from app.api.deps import require_admin

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/strategies/{strategy_id}/run")
def manual_run_strategy(
    strategy_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """전략 AI 분석 수동 실행 (백그라운드)."""
    strategy = db.get(Strategy, strategy_id)
    if not strategy or not strategy.is_active:
        raise HTTPException(status_code=404, detail="Strategy not found")

    def _run():
        from app.core.database import SessionLocal
        from app.services.trading.runner import StrategyRunner
        with SessionLocal() as sess:
            s = sess.get(Strategy, strategy_id)
            StrategyRunner(sess).run_strategy(s)

    background_tasks.add_task(_run)
    return {"message": f"Strategy '{strategy.name}' queued for execution"}


@router.post("/monitor")
def manual_monitor(
    background_tasks: BackgroundTasks,
    _: User = Depends(require_admin),
):
    """포지션 모니터링 수동 실행."""
    def _run():
        from app.core.database import SessionLocal
        from app.services.trading.executor import TradeExecutor
        with SessionLocal() as sess:
            TradeExecutor(sess).monitor_positions()

    background_tasks.add_task(_run)
    return {"message": "Position monitoring queued"}


@router.post("/verify")
def manual_verify(
    background_tasks: BackgroundTasks,
    _: User = Depends(require_admin),
):
    """추천 검증 수동 실행."""
    def _run():
        from app.core.database import SessionLocal
        from app.services.trading.verifier import run_verifications
        with SessionLocal() as sess:
            count = run_verifications(sess)
        return count

    background_tasks.add_task(_run)
    return {"message": "Verification queued"}


@router.get("/scheduler/status")
def scheduler_status(_: User = Depends(require_admin)):
    """스케줄러 상태 및 다음 실행 시각 조회."""
    import app.services.trading.scheduler as sched_module

    s = sched_module._scheduler
    if not s or not s.running:
        return {"running": False, "jobs": []}

    jobs = []
    for job in s.get_jobs():
        jobs.append({
            "id":       job.id,
            "trigger":  str(job.trigger),
            "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
        })
    return {"running": True, "jobs": jobs}


# ------------------------------------------------------------------ #
# 뉴스 감시 설정
# ------------------------------------------------------------------ #

@router.get("/news-watch/config")
def get_news_watch_config(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    from app.core.config_store import get_config
    interval_min = int(get_config(db, "news_check_interval_min", "40"))
    paused       = get_config(db, "news_auto_trade_paused", "false") == "true"
    pause_reason = get_config(db, "news_pause_reason", "")
    last_check   = get_config(db, "news_last_check_at", "")
    today_usage  = int(get_config(db, "news_today_usage", "0"))

    market_minutes = 6 * 60 + 30  # 09:00~15:30
    daily_checks   = max(1, market_minutes // interval_min)

    return {
        "interval_min":   interval_min,
        "paused":         paused,
        "pause_reason":   pause_reason,
        "last_check_at":  last_check,
        "today_usage":    today_usage,
        "daily_estimate": daily_checks,
        "rpd_limit":      20,
    }


@router.patch("/news-watch/config")
def update_news_watch_config(
    body: dict,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    from app.core.config_store import set_config
    interval_min = int(body.get("interval_min", 40))
    if interval_min < 30:
        raise HTTPException(status_code=400, detail="최소 주기는 30분입니다 (RPD 한도 초과 방지)")
    set_config(db, "news_check_interval_min", str(interval_min))
    return {"interval_min": interval_min}


@router.post("/news-watch/resume")
def resume_auto_trade(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    from app.core.config_store import set_config
    set_config(db, "news_auto_trade_paused", "false")
    set_config(db, "news_pause_reason", "")
    return {"message": "자동매매 재개됨"}


# ------------------------------------------------------------------ #
# Morning Gate
# ------------------------------------------------------------------ #

@router.get("/morning-gate/status")
def get_morning_gate_status(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """모닝 게이트 상태 조회."""
    from app.core.config_store import get_config
    paused = get_config(db, "morning_gate_paused", "false") == "true"
    reason = get_config(db, "morning_gate_reason", "")
    return {"paused": paused, "reason": reason}


@router.post("/morning-gate/resume")
def resume_morning_gate(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """모닝 게이트 수동 해제 (당일 매수 재개)."""
    from app.core.config_store import set_config
    set_config(db, "morning_gate_paused", "false")
    set_config(db, "morning_gate_reason", "")
    return {"message": "모닝 게이트 해제됨 — 자동매수 재개"}


@router.post("/thesis-check/trigger")
def trigger_thesis_check(
    background_tasks: BackgroundTasks,
    _: User = Depends(require_admin),
):
    """보유 포지션 thesis 재검증 수동 실행 (테스트용)."""
    def _run():
        from app.services.news.watcher import check_position_theses
        check_position_theses()
    background_tasks.add_task(_run)
    return {"message": "Thesis 재검증 시작됨"}


@router.post("/invalidation-check/trigger")
def trigger_invalidation_check(
    background_tasks: BackgroundTasks,
    _: User = Depends(require_admin),
):
    """관심종목 무효화_조건 자동 판정 수동 실행 (테스트용)."""
    def _run():
        from app.services.watchlist.invalidation import check_all_watchlist_invalidations
        check_all_watchlist_invalidations()
    background_tasks.add_task(_run)
    return {"message": "무효화 조건 체크 시작됨"}


@router.post("/watchlist-events/trigger")
def trigger_watchlist_event_scan(
    background_tasks: BackgroundTasks,
    force: bool = False,
    _: User = Depends(require_admin),
):
    """관심종목 이벤트 감지 수동 실행. force=true면 당일 수급/주가 감지 재실행
    (공시는 rcept_no 중복 방지가 항상 적용돼 재알림 없음)."""
    def _run():
        from app.services.watchlist.events import scan_watchlist_events
        scan_watchlist_events(force=force)
    background_tasks.add_task(_run)
    return {"message": "관심종목 이벤트 스캔 시작됨"}


@router.post("/morning-gate/trigger")
def trigger_morning_gate(
    background_tasks: BackgroundTasks,
    _: User = Depends(require_admin),
):
    """모닝 게이트 수동 트리거 (테스트용)."""
    def _run():
        from app.services.news.watcher import morning_gate_check
        morning_gate_check()
    background_tasks.add_task(_run)
    return {"message": "모닝 게이트 체크 시작됨"}


# ------------------------------------------------------------------ #
# Realtime Monitor 상태
# ------------------------------------------------------------------ #

@router.get("/realtime/status")
def get_realtime_status(_: User = Depends(require_admin)):
    """KIS WebSocket 및 realtime_monitor 상태 조회."""
    from app.services.kis.realtime import get_realtime_client
    from app.services.trading.realtime_monitor import get_monitor
    rt      = get_realtime_client()
    monitor = get_monitor()
    return {
        "kis_ws_connected":    rt.is_connected if rt else False,
        "subscribed_codes":    list(rt._subscribed) if rt else [],
        "monitor_holding_count": len(monitor._by_code) if monitor else 0,
    }


# ------------------------------------------------------------------ #
# Circuit Breaker
# ------------------------------------------------------------------ #

@router.get("/circuit-breaker/status")
def get_circuit_breaker_status(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """전체 유저의 circuit breaker 상태 조회."""
    from app.core.config_store import get_config
    from sqlalchemy import select as _select

    users = db.scalars(_select(User).where(User.is_active == True)).all()  # noqa: E712
    result = []
    for u in users:
        uid = str(u.user_id)
        paused = get_config(db, f"cb_paused_{uid}", "false") == "true"
        reason = get_config(db, f"cb_reason_{uid}", "")
        result.append({
            "user_id":  uid,
            "username": u.username,
            "paused":   paused,
            "reason":   reason,
        })
    return result


@router.post("/circuit-breaker/resume/{user_id}")
def resume_circuit_breaker(
    user_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """특정 유저의 circuit breaker 해제."""
    from app.core.config_store import set_config
    set_config(db, f"cb_paused_{user_id}", "false")
    set_config(db, f"cb_reason_{user_id}", "")
    return {"message": f"user={user_id} circuit breaker 해제됨"}

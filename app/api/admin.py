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

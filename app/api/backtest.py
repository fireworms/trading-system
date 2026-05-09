import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.recommendation import RecommendationRun
from app.models.strategy import Strategy
from app.models.user import User
from app.api.deps import get_current_user, require_admin
from app.services.trading.backtester import BacktestRunner

router = APIRouter(prefix="/admin/backtest", tags=["backtest"])


class BacktestRequest(BaseModel):
    base_date: date


@router.post("/strategies/{strategy_id}")
def run_backtest(
    strategy_id: uuid.UUID,
    req: BacktestRequest,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """전략 백테스트 실행 (동기, 완료까지 대기)."""
    strategy = db.get(Strategy, strategy_id)
    if not strategy or not strategy.is_active:
        raise HTTPException(status_code=404, detail="Strategy not found")

    runner = BacktestRunner(db)
    return runner.run_backtest(strategy, req.base_date)


@router.get("/strategies/{strategy_id}/results")
def get_backtest_results(
    strategy_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """저장된 백테스트 run 목록 조회."""
    runs = (
        db.query(RecommendationRun)
        .filter(
            RecommendationRun.strategy_id == strategy_id,
            RecommendationRun.is_backtest == True,
        )
        .order_by(RecommendationRun.run_date.desc())
        .all()
    )
    return [
        {
            "run_id": str(r.run_id),
            "run_date": str(r.run_date),
            "picks": len(r.recommendations),
            "verified": sum(1 for rec in r.recommendations if rec.verification),
            "success": sum(1 for rec in r.recommendations if rec.verification and rec.verification.result and rec.verification.result.value == "SUCCESS"),
        }
        for r in runs
    ]

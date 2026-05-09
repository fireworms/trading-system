import uuid
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import select, func

from app.core.database import get_db
from app.models.user import User
from app.models.recommendation import (
    RecommendationRun, Recommendation, MacroAnalysis,
    Verification, VerificationResult,
)
from app.schemas.recommendation import RecommendationRunOut, RecommendationOut, MacroAnalysisOut
from app.api.deps import get_current_user

router = APIRouter(prefix="/recommendations", tags=["recommendations"])


@router.get("/runs", response_model=list[RecommendationRunOut])
def list_runs(
    strategy_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    q = select(RecommendationRun).where(RecommendationRun.is_backtest == False)  # noqa: E712
    if strategy_id:
        q = q.where(RecommendationRun.strategy_id == strategy_id)
    return db.scalars(q.order_by(RecommendationRun.run_date.desc()).limit(50)).all()


@router.get("/runs/{run_id}", response_model=RecommendationRunOut)
def get_run(run_id: uuid.UUID, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    run = db.get(RecommendationRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@router.get("/runs/{run_id}/macro", response_model=MacroAnalysisOut)
def get_macro(run_id: uuid.UUID, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    analysis = db.scalar(select(MacroAnalysis).where(MacroAnalysis.run_id == run_id))
    if not analysis:
        raise HTTPException(status_code=404, detail="Macro analysis not found")
    return analysis


@router.get("/{rec_id}", response_model=RecommendationOut)
def get_recommendation(rec_id: uuid.UUID, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    rec = db.get(Recommendation, rec_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Recommendation not found")
    return rec


# ------------------------------------------------------------------ #
# 전략 통계 (대시보드용)
# ------------------------------------------------------------------ #

class StrategyStats(BaseModel):
    strategy_id: uuid.UUID
    total_runs: int
    total_picks: int
    total_verified: int
    success_count: int
    fail_count: int
    win_rate: float | None
    avg_pnl_pct: float | None
    success_avg_pnl: float | None
    fail_avg_pnl: float | None
    random_avg_pnl: float | None
    expected_value: float | None


@router.get("/stats/{strategy_id}", response_model=StrategyStats)
def get_strategy_stats(
    strategy_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """전략별 추천 성과 통계 (승률, 기댓값)."""
    live_filter = (
        RecommendationRun.strategy_id == strategy_id,
        RecommendationRun.is_backtest == False,  # noqa: E712
    )

    total_runs = db.scalar(
        select(func.count(RecommendationRun.run_id))
        .where(*live_filter)
    ) or 0

    total_picks = db.scalar(
        select(func.count(Recommendation.rec_id))
        .join(RecommendationRun)
        .where(*live_filter)
    ) or 0

    # 검증 결과
    rows = db.execute(
        select(Verification.result, func.count().label("cnt"), func.avg(Verification.pnl_pct).label("avg_pnl"))
        .join(Recommendation, Recommendation.rec_id == Verification.rec_id)
        .join(RecommendationRun, RecommendationRun.run_id == Recommendation.run_id)
        .where(*live_filter)
        .where(Verification.result != None)  # noqa: E711
        .group_by(Verification.result)
    ).all()

    result_map = {r.result: (r.cnt, float(r.avg_pnl or 0)) for r in rows}
    s_cnt, s_pnl = result_map.get(VerificationResult.SUCCESS, (0, 0.0))
    f_cnt, f_pnl = result_map.get(VerificationResult.FAIL,    (0, 0.0))
    total_verified = s_cnt + f_cnt

    win_rate = s_cnt / total_verified if total_verified > 0 else None
    avg_pnl  = (s_cnt * s_pnl + f_cnt * f_pnl) / total_verified if total_verified > 0 else None
    ev       = (win_rate * s_pnl + (1 - win_rate) * f_pnl) if win_rate is not None else None

    # 랜덤 대조군 평균 pnl (raw_response.random_baseline.avg_pnl 집계)
    runs_with_random = db.scalars(
        select(RecommendationRun).where(*live_filter)
    ).all()
    random_pnls = [
        r.raw_response["random_baseline"]["avg_pnl"]
        for r in runs_with_random
        if r.raw_response and r.raw_response.get("random_baseline", {}).get("avg_pnl") is not None
    ]
    random_avg_pnl = round(sum(random_pnls) / len(random_pnls), 4) if random_pnls else None

    return StrategyStats(
        strategy_id=strategy_id,
        total_runs=total_runs,
        total_picks=total_picks,
        total_verified=total_verified,
        success_count=s_cnt,
        fail_count=f_cnt,
        win_rate=win_rate,
        avg_pnl_pct=avg_pnl,
        success_avg_pnl=round(s_pnl, 4) if s_cnt > 0 else None,
        fail_avg_pnl=round(f_pnl, 4) if f_cnt > 0 else None,
        random_avg_pnl=random_avg_pnl,
        expected_value=ev,
    )

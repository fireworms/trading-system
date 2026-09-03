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

class RegimeStats(BaseModel):
    """시장 국면(KOSPI 20일선 위/아래)별 성과 분해."""
    state: str            # above / below / unknown
    verified: int
    win_rate: float | None
    avg_pnl_pct: float | None
    random_avg_pnl: float | None


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
    regime_breakdown: list[RegimeStats] = []


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

    # 국면별 분해 — 표본이 쪼개지므로 verified 건수를 반드시 함께 노출한다
    # (63건을 above/below로 나누면 각 30건, 승률 표준오차 ±9%p — 숫자만 보면 과신하기 쉽다)
    regime_rows = db.execute(
        select(
            RecommendationRun.kospi_ma20_state,
            Verification.result,
            func.count().label("cnt"),
            func.avg(Verification.pnl_pct).label("avg_pnl"),
        )
        .join(Recommendation, Recommendation.rec_id == Verification.rec_id)
        .join(RecommendationRun, RecommendationRun.run_id == Recommendation.run_id)
        .where(*live_filter)
        .where(Verification.result != None)  # noqa: E711
        .group_by(RecommendationRun.kospi_ma20_state, Verification.result)
    ).all()

    by_state: dict[str, dict] = {}
    for r in regime_rows:
        st = r.kospi_ma20_state or "unknown"
        acc = by_state.setdefault(st, {"s": 0, "f": 0, "s_pnl": 0.0, "f_pnl": 0.0})
        if r.result == VerificationResult.SUCCESS:
            acc["s"], acc["s_pnl"] = r.cnt, float(r.avg_pnl or 0)
        else:
            acc["f"], acc["f_pnl"] = r.cnt, float(r.avg_pnl or 0)

    # 국면별 랜덤 대조군 (run 단위 국면이라 run을 국면으로 갈라 평균)
    rand_by_state: dict[str, list[float]] = {}
    for r in runs_with_random:
        v = (r.raw_response or {}).get("random_baseline", {}).get("avg_pnl") if r.raw_response else None
        if v is not None:
            rand_by_state.setdefault(r.kospi_ma20_state or "unknown", []).append(v)

    regime_breakdown = []
    for st in ("above", "below", "unknown"):
        acc = by_state.get(st)
        if not acc:
            continue
        n = acc["s"] + acc["f"]
        if n == 0:
            continue
        rp = rand_by_state.get(st, [])
        regime_breakdown.append(RegimeStats(
            state=st,
            verified=n,
            win_rate=acc["s"] / n,
            avg_pnl_pct=round((acc["s"] * acc["s_pnl"] + acc["f"] * acc["f_pnl"]) / n, 4),
            random_avg_pnl=round(sum(rp) / len(rp), 4) if rp else None,
        ))

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
        regime_breakdown=regime_breakdown,
    )

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
    result = []
    for r in runs:
        verified     = [rec for rec in r.recommendations if rec.verification]
        success_recs = [rec for rec in verified if rec.verification.result and rec.verification.result.value == "SUCCESS"]
        fail_recs    = [rec for rec in verified if rec.verification.result and rec.verification.result.value == "FAIL"]
        all_pnls     = [float(rec.verification.pnl_pct) for rec in verified if rec.verification.pnl_pct is not None]
        success_pnls = [float(rec.verification.pnl_pct) for rec in success_recs if rec.verification.pnl_pct is not None]
        fail_pnls    = [float(rec.verification.pnl_pct) for rec in fail_recs if rec.verification.pnl_pct is not None]
        random_avg   = (r.raw_response or {}).get("random_avg_pnl")
        result.append({
            "run_id":          str(r.run_id),
            "run_date":        str(r.run_date),
            "picks":           len(r.recommendations),
            "verified":        len(verified),
            "success":         len(success_recs),
            "avg_pnl":         round(sum(all_pnls) / len(all_pnls), 4) if all_pnls else None,
            "success_avg_pnl": round(sum(success_pnls) / len(success_pnls), 4) if success_pnls else None,
            "fail_avg_pnl":    round(sum(fail_pnls) / len(fail_pnls), 4) if fail_pnls else None,
            "random_avg_pnl":  random_avg,
        })
    return result


@router.get("/strategies/{strategy_id}/summary")
def get_backtest_summary(
    strategy_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """백테스트 종합 통계 + 월별 추이 반환."""
    from collections import defaultdict

    runs = (
        db.query(RecommendationRun)
        .filter(
            RecommendationRun.strategy_id == strategy_id,
            RecommendationRun.is_backtest == True,
        )
        .order_by(RecommendationRun.run_date)
        .all()
    )

    all_ai_pnls: list[float] = []
    all_rand_avgs: list[float] = []
    monthly: dict[str, dict] = defaultdict(lambda: {"ai": [], "rand": [], "success": 0})

    for r in runs:
        verified     = [rec for rec in r.recommendations if rec.verification]
        success_recs = [rec for rec in verified if rec.verification.result and rec.verification.result.value == "SUCCESS"]
        ai_pnls      = [float(rec.verification.pnl_pct) for rec in verified if rec.verification.pnl_pct is not None]
        rand_avg     = (r.raw_response or {}).get("random_avg_pnl")

        all_ai_pnls.extend(ai_pnls)
        if rand_avg is not None:
            all_rand_avgs.append(rand_avg)

        ym = r.run_date.strftime("%Y-%m")
        monthly[ym]["ai"].extend(ai_pnls)
        monthly[ym]["success"] += len(success_recs)
        if rand_avg is not None:
            monthly[ym]["rand"].append(rand_avg)

    def _avg(lst: list[float]) -> float | None:
        return round(sum(lst) / len(lst), 4) if lst else None

    ai_success = [p for p in all_ai_pnls if p > 0]
    ai_fail    = [p for p in all_ai_pnls if p <= 0]
    ai_avg     = _avg(all_ai_pnls)
    rand_avg_all = _avg(all_rand_avgs)

    monthly_list = []
    for ym in sorted(monthly):
        d = monthly[ym]
        ai_a = _avg(d["ai"])
        ra   = _avg(d["rand"])
        total = len(d["ai"])
        monthly_list.append({
            "month":       ym,
            "picks":       total,
            "win_rate":    round(d["success"] / total, 4) if total else None,
            "ai_avg_pnl":  ai_a,
            "rand_avg_pnl": ra,
            "advantage":   round(ai_a - ra, 4) if ai_a is not None and ra is not None else None,
        })

    return {
        "total_runs":      len(runs),
        "total_picks":     len(all_ai_pnls),
        "win_rate":        round(len(ai_success) / len(all_ai_pnls), 4) if all_ai_pnls else None,
        "ai_avg_pnl":      ai_avg,
        "ai_success_avg":  _avg(ai_success),
        "ai_fail_avg":     _avg(ai_fail),
        "rand_avg_pnl":    rand_avg_all,
        "advantage":       round(ai_avg - rand_avg_all, 4) if ai_avg is not None and rand_avg_all is not None else None,
        "monthly":         monthly_list,
    }

"""
추천 종목 검증 시스템.
추천일로부터 hold_days 경과 시 실제 결과를 자동 검증하고,
해당 프롬프트 버전의 performance_score를 갱신한다.
"""
import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy.orm import Session
from sqlalchemy import select, func

from app.models.recommendation import (
    Recommendation, Verification, VerificationResult,
    RecommendationRun, PromptVersion,
)
from app.models.strategy import Strategy
from app.services.kis.client import get_kis_client

logger = logging.getLogger(__name__)


def run_verifications(db: Session) -> int:
    """
    검증 대상 추천 종목을 찾아 결과를 기록하고 성과 점수를 갱신한다.
    검증 대상: verification이 없고, run_date + hold_days <= today
    반환값: 검증 처리 건수
    """
    today = date.today()
    client = get_kis_client(db)
    count = 0
    affected_versions: set[str] = set()

    recs = db.scalars(
        select(Recommendation)
        .join(Recommendation.run)
        .outerjoin(Recommendation.verification)
        .where(Verification.verify_id == None)   # noqa: E711
        .where(Recommendation.target_price != None)  # noqa: E711
    ).all()

    for rec in recs:
        run: RecommendationRun = rec.run
        strategy: Strategy = run.strategy

        if run.run_date + timedelta(days=strategy.hold_days) > today:
            continue

        try:
            result = _verify_recommendation(rec, run, strategy, client, today)
            db.add(result)
            count += 1
            if run.prompt_version:
                affected_versions.add(run.prompt_version)
        except Exception as e:
            logger.error("Verification failed for rec=%s: %s", rec.rec_id, e)

    db.commit()
    logger.info("Verifications completed: %d", count)

    # 영향받은 프롬프트 버전 성과 점수 갱신
    for version_no in affected_versions:
        _update_performance_score(db, version_no)

    return count


def _verify_recommendation(
    rec: Recommendation,
    run: RecommendationRun,
    strategy: Strategy,
    client,
    today: date,
) -> Verification:
    current_price = client.get_current_price(rec.stock_code)
    bars = client.get_ohlcv(rec.stock_code)

    # 검증 기간: run_date ~ run_date + hold_days (이후 데이터 제외)
    period_start = str(run.run_date)
    period_end   = str(run.run_date + timedelta(days=strategy.hold_days))
    relevant = [b for b in bars if period_start <= b.date <= period_end]

    max_high = max((b.high for b in relevant), default=current_price)
    max_low  = min((b.low  for b in relevant), default=current_price)

    # 목표가 도달 여부: 보유기간 내 고점이 target_price 이상이면 SUCCESS
    verdict = VerificationResult.FAIL
    if rec.target_price and max_high >= rec.target_price:
        verdict = VerificationResult.SUCCESS

    # pnl_pct: 추천 당시 현재가 대비 hold_days 후 가격 변화
    # current_price_at_rec이 없으면 현재가로 fallback (부정확하지만 최선)
    entry = rec.current_price_at_rec or current_price
    pnl = (
        (current_price - entry) / entry * 100
        if entry and entry > 0
        else Decimal("0")
    )

    return Verification(
        rec_id=rec.rec_id,
        verified_at=datetime.now(timezone.utc),
        price_at_verify=current_price,
        max_high=max_high,
        max_low=max_low,
        result=verdict,
        pnl_pct=Decimal(str(round(float(pnl), 4))),
    )


def _update_performance_score(db: Session, version_no: str) -> None:
    """
    version_no에 해당하는 모든 추천의 검증 결과로 성과 점수를 계산해
    prompt_versions 테이블을 갱신한다.
    performance_score = 성공 건수 / 전체 검증 건수 (0.0 ~ 1.0)
    """
    rows = db.execute(
        select(
            Verification.result,
            func.count().label("cnt"),
        )
        .join(Recommendation, Recommendation.rec_id == Verification.rec_id)
        .join(RecommendationRun, RecommendationRun.run_id == Recommendation.run_id)
        .where(RecommendationRun.prompt_version == version_no)
        .group_by(Verification.result)
    ).all()

    total   = sum(r.cnt for r in rows)
    success = next((r.cnt for r in rows if r.result == VerificationResult.SUCCESS), 0)

    if total == 0:
        return

    score = Decimal(str(round(success / total, 4)))

    db.execute(
        PromptVersion.__table__.update()
        .where(PromptVersion.version_no == version_no)
        .values(performance_score=score)
    )
    db.commit()
    logger.info(
        "PromptVersion %s score updated: %.1f%% (%d/%d)",
        version_no, float(score) * 100, success, total,
    )


def recalculate_all_scores(db: Session) -> dict:
    """모든 프롬프트 버전의 성과 점수를 재계산한다 (관리자 수동 실행용)."""
    versions = db.scalars(
        select(PromptVersion.version_no).distinct()
    ).all()

    results = {}
    for version_no in versions:
        _update_performance_score(db, version_no)
        pv = db.scalars(
            select(PromptVersion).where(PromptVersion.version_no == version_no).limit(1)
        ).first()
        results[version_no] = float(pv.performance_score) if pv and pv.performance_score else None

    return results

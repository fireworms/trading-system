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


def simulate_exit_pnl(
    bars,
    entry,
    target_pct: Decimal,
    stop_loss_pct: Decimal,
    period_start: str,
    period_end: str,
) -> float | None:
    """전략 청산 규칙(목표/손절/만기)을 일봉에 적용해 pnl%를 계산한다.

    AI 픽 검증과 랜덤 대조군이 **같은 청산 모델**을 쓰도록 하는 단일 진실 공급원.
    - 손절-우선: 같은 날 목표·손절 둘 다 터치 시 손절 (보수적 convention)
    - 어느 쪽도 미터치 시 기간 마지막 봉 종가로 청산
    - 목표/손절가는 runner가 Recommendation에 박는 방식과 동일하게 정수 반올림
    - 분할/상폐 등 데이터 오류 대비 ±클램프(-100~+200%)
    반환값: pnl% (계산 불가 시 None)
    """
    if not bars or entry is None:
        return None
    entry_d = Decimal(str(entry))
    if entry_d <= 0:
        return None

    relevant = sorted(
        [b for b in bars if period_start <= b.date <= period_end],
        key=lambda b: b.date,
    )
    if not relevant:
        return None

    target = (entry_d * (1 + Decimal(str(target_pct)) / 100)).quantize(Decimal("1"))
    stop   = (entry_d * (1 - Decimal(str(stop_loss_pct)) / 100)).quantize(Decimal("1"))

    exit_price = relevant[-1].close
    for bar in relevant:
        if bar.low <= stop:
            exit_price = stop
            break
        if bar.high >= target:
            exit_price = target
            break

    pnl = (float(exit_price) - float(entry_d)) / float(entry_d) * 100
    return max(-100.0, min(200.0, pnl))


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

    # 랜덤 대조군 pnl 계산 (raw_response에 entries가 있고 아직 avg_pnl 없는 run)
    _verify_random_baselines(db, client, today)

    return count


def _verify_random_baselines(db, client, today: date) -> None:
    """raw_response.random_baseline.entries가 있는 run에 대해 랜덤 대조군 pnl 계산.

    AI 픽과 **완전히 동일한 청산 규칙**(simulate_exit_pnl)을 적용한다.
    과거에는 목표/손절 없이 만기 종가만 썼는데, 그러면 AI 우위 지표가
    종목 선정력이 아니라 손절 로직의 효과를 재게 된다.
    """
    from sqlalchemy import select as sa_select
    runs = db.scalars(
        sa_select(RecommendationRun)
        .where(RecommendationRun.is_backtest == False)  # noqa: E712
        .where(RecommendationRun.raw_response != None)   # noqa: E711
    ).all()

    updated = 0
    for run in runs:
        raw = run.raw_response or {}
        baseline = raw.get("random_baseline", {})
        if not baseline.get("entries") or baseline.get("avg_pnl") is not None:
            continue  # 이미 계산됐거나 entries 없음

        strategy: Strategy = run.strategy
        if run.run_date + timedelta(days=strategy.hold_days) > today:
            continue  # hold_days 미경과

        # 기간 경계는 AI 검증과 동일하게 run_date 당일 봉 포함
        # (08:30 실행이라 진입가는 전일 종가 → 당일 봉은 전부 미래 구간)
        period_start = run.run_date.strftime("%Y%m%d")
        period_end   = (run.run_date + timedelta(days=strategy.hold_days)).strftime("%Y%m%d")
        pnls = []
        for code, entry_price in baseline["entries"].items():
            try:
                pnl = simulate_exit_pnl(
                    client.get_ohlcv(code), entry_price,
                    strategy.target_pct, strategy.stop_loss_pct,
                    period_start, period_end,
                )
                if pnl is not None:
                    pnls.append(pnl)
            except Exception as e:
                logger.warning("Random baseline verify failed for %s: %s", code, e)

        if pnls:
            run.raw_response = {
                **raw,
                "random_baseline": {
                    **baseline,
                    "avg_pnl": round(sum(pnls) / len(pnls), 4),
                    "sample_n": len(pnls),
                },
            }
            updated += 1

    if updated:
        db.commit()
        logger.info("Random baseline pnl updated for %d runs", updated)


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
    # get_ohlcv() bar.date는 "YYYYMMDD" 포맷 — ISO 포맷과 문자열 비교 시 항상 불일치하므로 맞춰야 함
    period_start = run.run_date.strftime("%Y%m%d")
    period_end   = (run.run_date + timedelta(days=strategy.hold_days)).strftime("%Y%m%d")
    relevant = sorted(
        [b for b in bars if period_start <= b.date <= period_end],
        key=lambda b: b.date,
    )

    max_high = max((b.high for b in relevant), default=current_price)
    max_low  = min((b.low  for b in relevant), default=current_price)

    # 날짜순 순회 — 손절가와 목표가 중 먼저 터치되는 쪽으로 판정
    # 같은 날 둘 다 터치: 손절 우선 (보수적 convention)
    verdict    = VerificationResult.FAIL
    exit_price = relevant[-1].close if relevant else current_price

    for bar in relevant:
        stop_hit   = rec.stop_loss_price and bar.low  <= rec.stop_loss_price
        target_hit = rec.target_price    and bar.high >= rec.target_price

        if stop_hit:
            exit_price = rec.stop_loss_price
            break
        if target_hit:
            verdict    = VerificationResult.SUCCESS
            exit_price = rec.target_price
            break
    else:
        # 기간 내 어느 쪽도 안 터치 → 마지막 봉 종가로 pnl 계산
        exit_price = relevant[-1].close if relevant else current_price

    entry = rec.current_price_at_rec or current_price
    pnl = (
        (float(exit_price) - float(entry)) / float(entry) * 100
        if entry and float(entry) > 0
        else 0.0
    )
    # 분할/상폐 등 데이터 오류로 인한 비현실적 수익률 클램프 (KR 일일 등락 ±30%)
    if pnl > 200 or pnl < -100:
        logger.warning("Clamp implausible pnl %.1f%% for rec %s (split/bad data?)", pnl, rec.rec_id)
        pnl = max(-100.0, min(200.0, pnl))

    return Verification(
        rec_id=rec.rec_id,
        verified_at=datetime.now(timezone.utc),
        price_at_verify=current_price,
        max_high=max_high,
        max_low=max_low,
        result=verdict,
        pnl_pct=Decimal(str(round(pnl, 4))),
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

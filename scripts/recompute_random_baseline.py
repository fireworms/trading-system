"""
라이브 run의 랜덤 대조군(random_baseline)을 소급 재계산한다.

배경:
  verifier._verify_random_baselines가 예전에는 목표/손절 없이 만기 종가만 썼다.
  그 결과 랜덤 쪽만 단순 보유 수익률이 되어, 대시보드의 "AI 우위"가
  종목 선정력이 아니라 손절 로직의 효과를 재고 있었다.
  (KOSPI 대형주 스윙 랜덤 -4.05% — 손절 -3% 규칙에선 나올 수 없는 값)

이 스크립트가 하는 일:
  1) 라이브 run(is_backtest=False)의 random_baseline.avg_pnl을 폐기
  2) raw_response.price_snapshot(수집 시점 전 종목 가격)에서 표본을 확대 재추출
     — 기존 entries는 pick_count(=3)개뿐이라 벤치마크 자체의 오차가 컸다
     — 재현 가능하도록 run_id를 시드로 사용 (재실행해도 같은 표본)
  3) verifier.simulate_exit_pnl로 AI 픽과 동일한 청산 규칙 적용해 재계산

사용법:
  .venv/bin/python -m scripts.recompute_random_baseline           # 실제 반영
  .venv/bin/python -m scripts.recompute_random_baseline --dry-run # 미리보기
"""
import argparse
import logging
import random
import sys
from datetime import date, timedelta

from app.core.database import SessionLocal
from app.models.recommendation import RecommendationRun
from app.models.strategy import Strategy
from app.services.kis.client import get_kis_client
from app.services.trading.verifier import simulate_exit_pnl
from app.services.trading.runner import _RANDOM_BASELINE_N

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)  # 종목별 요청 로그가 결과표를 덮는 것 방지


def _sample_entries(run: RecommendationRun) -> dict[str, float]:
    """price_snapshot 우선으로 랜덤 표본 재추출. 없으면 기존 entries 유지."""
    raw = run.raw_response or {}
    snapshot = raw.get("price_snapshot") or {}
    existing = (raw.get("random_baseline") or {}).get("entries") or {}

    pool = {c: float(p) for c, p in snapshot.items() if p and float(p) > 0}
    if not pool:
        return {c: float(p) for c, p in existing.items() if p and float(p) > 0}

    codes = sorted(pool.keys())                    # 정렬 → 시드만으로 재현 보장
    rng = random.Random(str(run.run_id))
    picked = rng.sample(codes, min(_RANDOM_BASELINE_N, len(codes)))
    return {c: pool[c] for c in picked}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="DB 쓰지 않고 결과만 출력")
    args = ap.parse_args()

    today = date.today()
    db = SessionLocal()
    client = get_kis_client(db)
    changed = skipped = 0

    try:
        runs = (
            db.query(RecommendationRun)
            .filter(RecommendationRun.is_backtest == False)  # noqa: E712
            .filter(RecommendationRun.raw_response != None)  # noqa: E711
            .order_by(RecommendationRun.run_date)
            .all()
        )
        logger.info("라이브 run %d건 검사", len(runs))

        for run in runs:
            raw = run.raw_response or {}
            if "random_baseline" not in raw:
                continue

            strategy: Strategy = run.strategy
            entries = _sample_entries(run)
            if not entries:
                skipped += 1
                continue

            if run.run_date + timedelta(days=strategy.hold_days) > today:
                # hold 기간 미경과 → pnl은 00:10 잡이 계산.
                # 다만 표본(entries)은 지금 확장해 둬야 그 잡이 40개로 계산한다.
                if not args.dry_run:
                    run.raw_response = {
                        **raw,
                        "random_baseline": {**(raw.get("random_baseline") or {}), "entries": entries},
                    }
                skipped += 1
                continue

            period_start = run.run_date.strftime("%Y%m%d")
            period_end = (run.run_date + timedelta(days=strategy.hold_days)).strftime("%Y%m%d")

            pnls = []
            for code, entry in entries.items():
                try:
                    pnl = simulate_exit_pnl(
                        client.get_ohlcv(code), entry,
                        strategy.target_pct, strategy.stop_loss_pct,
                        period_start, period_end,
                    )
                    if pnl is not None:
                        pnls.append(pnl)
                except Exception as e:
                    logger.warning("  %s 계산 실패: %s", code, e)

            if not pnls:
                skipped += 1
                continue

            old = (raw.get("random_baseline") or {}).get("avg_pnl")
            new = round(sum(pnls) / len(pnls), 4)
            logger.info(
                "%s %-28s %s → %s  (n=%d, 목표 +%s%% / 손절 -%s%%)",
                run.run_date, strategy.name[:28],
                f"{old:+.2f}%" if old is not None else "미계산",
                f"{new:+.2f}%", len(pnls),
                strategy.target_pct, strategy.stop_loss_pct,
            )

            # 범위 검증: 청산 규칙이 제대로 걸렸으면 평균은 [-손절%, +목표%] 안에 갇힌다
            # 목표/손절가를 원 단위로 반올림하므로 경계를 소수점 이하로 넘을 수 있다 (0.5%p 여유)
            lo, hi = -float(strategy.stop_loss_pct) - 0.5, float(strategy.target_pct) + 0.5
            if not (lo <= new <= hi):
                logger.error("  ⚠ 범위 이탈! %+.2f%% not in [%+.1f%%, %+.1f%%]", new, lo, hi)

            if not args.dry_run:
                run.raw_response = {
                    **raw,
                    "random_baseline": {
                        "entries": entries,
                        "avg_pnl": new,
                        "sample_n": len(pnls),
                    },
                }
            changed += 1

        if args.dry_run:
            logger.info("[dry-run] 반영 안 함 — 재계산 %d건 / 스킵 %d건", changed, skipped)
        else:
            db.commit()
            logger.info("완료: 재계산 %d건 / 스킵 %d건", changed, skipped)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

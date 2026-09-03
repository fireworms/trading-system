"""
기존 recommendation_runs에 시장 국면(KOSPI 20일선 위/아래)을 소급 기록한다.

KOSPI 일봉을 **한 번만** 조회해 date→close 맵으로 캐시하고, run_date별로
로컬 계산한다 (run마다 지수를 다시 긁으면 낭비 + rate limit 소모).

사용법:
  .venv/bin/python -m scripts.backfill_market_regime            # 실제 반영
  .venv/bin/python -m scripts.backfill_market_regime --dry-run  # 미리보기
  .venv/bin/python -m scripts.backfill_market_regime --force    # 이미 채워진 run도 재계산
"""
import argparse
import logging
import sys
from collections import Counter
from decimal import Decimal

from app.core.database import SessionLocal
from app.models.recommendation import RecommendationRun
from app.services.kis.client import get_kis_client
from app.services.trading.market_regime import (
    MA_PERIOD, fetch_kospi_history, regime_from_history,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="DB 쓰지 않고 결과만 출력")
    ap.add_argument("--force", action="store_true", help="이미 국면이 기록된 run도 재계산")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        runs = (
            db.query(RecommendationRun)
            .order_by(RecommendationRun.run_date)
            .all()
        )
        if not runs:
            logger.info("대상 run 없음")
            return 0

        oldest = min(r.run_date for r in runs)
        span_days = (max(r.run_date for r in runs) - oldest).days
        # 가장 오래된 run에서도 MA20을 계산하려면 그 이전 20거래일이 더 필요하다
        need = span_days + MA_PERIOD * 2 + 30
        logger.info("run %d건 (%s ~ %s) → KOSPI 일봉 %d거래일 조회",
                    len(runs), oldest, max(r.run_date for r in runs), need)

        client = get_kis_client(db)
        history = fetch_kospi_history(client, need)
        if not history:
            logger.error("KOSPI 일봉 조회 실패 — 중단")
            return 1
        logger.info("일봉 %d건 확보 (%s ~ %s)", len(history), history[-1][0], history[0][0])

        filled = skipped = 0
        states = Counter()
        for run in runs:
            if run.kospi_ma20_state and not args.force:
                skipped += 1
                continue

            regime = regime_from_history(history, run.run_date)
            if not regime:
                logger.warning("%s %s — 일봉 부족으로 판정 불가", run.run_date, run.strategy.name[:24])
                skipped += 1
                continue

            states[regime["state"]] += 1
            filled += 1
            if not args.dry_run:
                run.kospi_close = Decimal(str(regime["close"]))
                run.kospi_ma20 = Decimal(str(regime["ma20"]))
                run.kospi_ma20_state = regime["state"]

        logger.info("국면 분포: above=%d / below=%d", states["above"], states["below"])
        if args.dry_run:
            logger.info("[dry-run] 반영 안 함 — 채움 %d건 / 스킵 %d건", filled, skipped)
        else:
            db.commit()
            logger.info("완료: 채움 %d건 / 스킵 %d건", filled, skipped)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

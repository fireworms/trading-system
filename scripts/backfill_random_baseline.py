"""
기존 백테스트 run 중 random_avg_pnl이 없는 것들을 소급 계산.

전략:
- stock_master에서 run마다 랜덤 pick_count개 샘플링
- get_historical_stock_info(code, run_date)로 진입가 획득
- get_ohlcv(code, days=100)로 hold 기간 말 종가 계산
- raw_response에 random_avg_pnl 업데이트
"""
import random
import logging
import sys
import time
from datetime import date, timedelta

sys.path.insert(0, "/home/firew/trading_system")

from app.core.database import SessionLocal
from app.models.recommendation import RecommendationRun
from app.models.stock_master import StockMaster
from app.models.strategy import Strategy
from app.services.kis.client import get_kis_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SAMPLE_MULTIPLIER = 20  # pick_count × 20개 후보 중에서 시도, 충분한 진입가 확보
MIN_CANDIDATES = 50
MAX_CANDIDATES = 200


def _get_random_pnl(client, stock_code: str, run_date: date, hold_days: int) -> float | None:
    """단일 종목 run_date 진입가 → hold_days 후 종가 pnl 계산.
    get_ohlcv 한 번만 호출해서 진입가 + 종가 모두 추출.
    """
    try:
        bars = client.get_ohlcv(stock_code, days=100)
        if not bars:
            return None

        run_date_str = run_date.strftime("%Y%m%d")
        period_end = (run_date + timedelta(days=hold_days)).strftime("%Y%m%d")

        # run_date 이전 마지막 봉 = 진입가(종가)
        entry_bars = sorted([b for b in bars if b.date <= run_date_str], key=lambda b: b.date)
        if not entry_bars:
            return None
        entry = float(entry_bars[-1].close)
        if entry <= 0:
            return None

        # run_date 이후 ~ period_end 봉 = hold 기간
        future = sorted([b for b in bars if run_date_str < b.date <= period_end], key=lambda b: b.date)
        if not future:
            return None

        end_price = float(future[-1].close)
        return round((end_price - entry) / entry * 100, 4)
    except Exception as e:
        logger.debug("pnl calc failed %s: %s", stock_code, e)
        return None


def backfill(dry_run: bool = False) -> None:
    db = SessionLocal()
    client = get_kis_client(db)

    # 소급 대상 run 수집
    runs = (
        db.query(RecommendationRun)
        .filter(RecommendationRun.is_backtest == True)  # noqa: E712
        .order_by(RecommendationRun.run_date)
        .all()
    )
    targets = [
        r for r in runs
        if isinstance(r.raw_response, dict) and "random_avg_pnl" not in r.raw_response
    ]
    logger.info("소급 대상: %d개 run", len(targets))

    # 전략별 hold_days / pick_count 캐시
    strategy_cache: dict[str, Strategy] = {}

    # 종목 풀 (run마다 샘플링하되 stock_master는 한 번만 로드)
    all_stocks = db.query(StockMaster).filter(
        StockMaster.is_active == True,  # noqa: E712
        StockMaster.country == "KR",
    ).all()
    logger.info("stock_master KR 활성 종목 수: %d", len(all_stocks))

    updated = 0
    skipped = 0

    for i, run in enumerate(targets, 1):
        sid = str(run.strategy_id)
        if sid not in strategy_cache:
            strat = db.query(Strategy).filter(Strategy.strategy_id == run.strategy_id).first()
            strategy_cache[sid] = strat
        strat = strategy_cache[sid]
        pick_count = strat.pick_count if strat else 5
        hold_days = strat.hold_days if strat else 10

        logger.info("[%d/%d] run=%s date=%s hold=%dd pick=%d",
                    i, len(targets), str(run.run_id)[:8], run.run_date, hold_days, pick_count)

        # 충분한 후보 샘플링 (진입가 없는 종목 대비 여유분 확보)
        sample_size = min(max(pick_count * SAMPLE_MULTIPLIER, MIN_CANDIDATES), MAX_CANDIDATES)
        step = max(1, len(all_stocks) // sample_size)
        # run마다 다른 시작점으로 다양성 확보
        start = (i * 7) % max(1, step)
        pool = all_stocks[start::step][:sample_size]
        random.shuffle(pool)

        pnls: list[float] = []
        attempted = 0
        for stock in pool:
            if len(pnls) >= pick_count:
                break
            attempted += 1
            pnl = _get_random_pnl(client, stock.stock_code, run.run_date, hold_days)
            if pnl is not None:
                pnls.append(pnl)

        logger.info("  시도=%d 성공=%d pnls=%s", attempted, len(pnls),
                    [round(p, 2) for p in pnls])

        if not pnls:
            logger.warning("  pnl 계산 실패 — skip")
            skipped += 1
            continue

        avg_pnl = round(sum(pnls) / len(pnls), 4)
        logger.info("  random_avg_pnl = %.4f%%", avg_pnl)

        if not dry_run:
            run.raw_response = {**(run.raw_response or {}), "random_avg_pnl": avg_pnl}
            db.commit()
            updated += 1

        # KIS API 부하 방지
        time.sleep(0.3)

    logger.info("완료: updated=%d skipped=%d dry_run=%s", updated, skipped, dry_run)
    db.close()


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    backfill(dry_run=dry)

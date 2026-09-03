"""
KRX 일별 전종목 시세 벌크 적재 (daily_price).

KRX는 기간 조회 파라미터가 없어 `basDd` 하루씩 호출해야 한다 (명세서 확인).
하루 = KOSPI 943 + KOSDAQ 1822 ≈ 2765행, 호출 2회.

진행 상황을 app_config(`krx_load_last_date`)에 저장해 중단 시 이어받는다.
주말은 API 호출 없이 로컬에서 스킵하고, 공휴일은 `200 + 0행`으로 응답되므로
그대로 "거래일 아님"으로 처리한다.

**호출 제한은 명세서에 없다** — 보수적으로 sleep 0.5초로 시작하고,
연속 실패가 누적되면 자동으로 대기를 늘린다(백오프). 첫 실행은 --months 1로
한 달만 받아 안전성을 확인한 뒤 확대할 것.

사용법:
  # 첫 실행 — 최근 1개월만 (rate limit 탐색)
  .venv/bin/python -m scripts.load_krx_daily --months 1

  # 이어받기 (저장된 진행 지점부터 오늘까지)
  .venv/bin/python -m scripts.load_krx_daily --resume

  # 구간 지정
  .venv/bin/python -m scripts.load_krx_daily --start 2010-01-04 --end 2015-12-31

  # 진행 상황만 확인
  .venv/bin/python -m scripts.load_krx_daily --status
"""
import argparse
import logging
import sys
import time
from datetime import date, datetime, timedelta

from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.config_store import get_config, set_config
from app.core.database import SessionLocal
from app.models.daily_price import DailyPrice
from app.services.krx.client import KRXAuthError, get_daily_trades

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

PROGRESS_KEY = "krx_load_last_date"     # 여기까지 적재 완료 (YYYY-MM-DD)
KRX_DATA_START = date(2010, 1, 4)       # KRX 제공 시작일 (명세서)

_DEFAULT_SLEEP = 0.5
_MAX_SLEEP = 8.0
_MAX_CONSEC_FAILURES = 5


def _upsert_day(db, rows: list[dict]) -> int:
    """하루치 전종목 upsert. 재적재 시 최신 응답으로 갱신."""
    if not rows:
        return 0

    payload = []
    for r in rows:
        payload.append({
            "stock_code":    r["stock_code"],
            "trade_date":    datetime.strptime(r["trade_date"], "%Y%m%d").date(),
            "stock_name":    r["stock_name"] or None,
            "market":        r["market"] or None,
            "sector_type":   r["sector_type"],
            "open":          r["open"],
            "high":          r["high"],
            "low":           r["low"],
            "close":         r["close"],
            "change":        r["change"],
            "change_pct":    r["change_pct"],
            "volume":        r["volume"],
            "trade_value":   r["trade_value"],
            "market_cap":    r["market_cap"],
            "listed_shares": r["listed_shares"],
        })

    stmt = pg_insert(DailyPrice).values(payload)
    update_cols = {
        c: stmt.excluded[c] for c in (
            "stock_name", "market", "sector_type", "open", "high", "low", "close",
            "change", "change_pct", "volume", "trade_value", "market_cap", "listed_shares",
        )
    }
    db.execute(stmt.on_conflict_do_update(
        index_elements=["stock_code", "trade_date"], set_=update_cols
    ))
    return len(payload)


def _resolve_range(args, db) -> tuple[date, date]:
    today = date.today()
    if args.start:
        start = datetime.strptime(args.start, "%Y-%m-%d").date()
    elif args.resume:
        saved = get_config(db, PROGRESS_KEY)
        if not saved:
            logger.error("저장된 진행 지점이 없다. --months 또는 --start로 시작할 것.")
            sys.exit(1)
        start = datetime.strptime(saved, "%Y-%m-%d").date() + timedelta(days=1)
    elif args.months:
        start = today - timedelta(days=int(args.months * 30.5))
    else:
        logger.error("--months / --start / --resume 중 하나를 지정할 것.")
        sys.exit(1)

    end = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end else today
    return max(start, KRX_DATA_START), min(end, today)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", help="시작일 YYYY-MM-DD")
    ap.add_argument("--end", help="종료일 YYYY-MM-DD (기본: 오늘)")
    ap.add_argument("--months", type=float, help="최근 N개월치")
    ap.add_argument("--resume", action="store_true", help="저장된 진행 지점부터 이어받기")
    ap.add_argument("--status", action="store_true", help="진행 상황만 출력")
    ap.add_argument("--sleep", type=float, default=_DEFAULT_SLEEP, help="요청 간 대기 초 (기본 0.5)")
    ap.add_argument("--dry-run", action="store_true", help="DB 쓰지 않고 조회만")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        if args.status:
            saved = get_config(db, PROGRESS_KEY)
            total = db.query(DailyPrice).count()
            rng = db.query(
                DailyPrice.trade_date.label("d")
            ).order_by(DailyPrice.trade_date).first()
            last = db.query(DailyPrice.trade_date).order_by(
                DailyPrice.trade_date.desc()).first()
            logger.info("진행 지점: %s", saved or "없음")
            logger.info("적재 행수: %s", f"{total:,}")
            if rng and last:
                logger.info("적재 구간: %s ~ %s", rng.d, last[0])
            return 0

        start, end = _resolve_range(args, db)
        if start > end:
            logger.info("적재할 구간 없음 (start=%s > end=%s) — 이미 최신", start, end)
            return 0

        logger.info("적재 구간: %s ~ %s (요청 간 %.1f초)", start, end, args.sleep)

        sleep_s = args.sleep
        consec_failures = 0
        cur = start
        days_done = holidays = 0
        total_rows = 0

        while cur <= end:
            if cur.weekday() >= 5:      # 토·일은 호출 없이 스킵
                cur += timedelta(days=1)
                continue

            try:
                res = get_daily_trades(cur)
            except KRXAuthError as e:
                logger.error("이용신청 문제로 중단: %s", e)
                break
            except Exception as e:
                consec_failures += 1
                sleep_s = min(sleep_s * 2, _MAX_SLEEP)
                logger.warning("%s 조회 실패(%d회 연속): %s → 대기 %.1f초로 증가",
                               cur, consec_failures, e, sleep_s)
                if consec_failures >= _MAX_CONSEC_FAILURES:
                    logger.error("연속 실패 %d회 — 중단. --resume으로 이어받을 것.",
                                 consec_failures)
                    break
                time.sleep(sleep_s)
                continue

            consec_failures = 0
            rows = res["rows"]
            if not rows:
                holidays += 1               # 공휴일 = 200 + 0행 (에러 아님)
            else:
                if not args.dry_run:
                    n = _upsert_day(db, rows)
                    total_rows += n
                    set_config(db, PROGRESS_KEY, cur.isoformat())
                    db.commit()
                else:
                    total_rows += len(rows)
                days_done += 1
                if days_done % 20 == 0:
                    logger.info("  ... %s 까지 %d거래일 / %s행", cur, days_done, f"{total_rows:,}")

            cur += timedelta(days=1)
            time.sleep(sleep_s)

        tag = "[dry-run] " if args.dry_run else ""
        logger.info("%s완료: %d거래일 적재 / %s행 / 휴장 %d일",
                    tag, days_done, f"{total_rows:,}", holidays)
        if not args.dry_run:
            logger.info("진행 지점 저장됨: %s", get_config(db, PROGRESS_KEY))
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

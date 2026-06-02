"""
stock_master.sector 백필 — KIS CTPF1002R(search-stock-info)로 종목별 업종 채움.

- 대상: 활성 KR 종목(KOSPI/KOSDAQ). NAS는 CTPF1002R 미지원이라 제외
- 재실행 안전: 기본은 sector가 비어있는 것만 채움(--all 주면 전체 갱신)
- KIS rate limit은 client._RateLimiter(18/초) 전역 적용되어 별도 sleep 불필요

실행: .venv/bin/python scripts/backfill_sectors.py [--all]
"""
import logging
import sys

sys.path.insert(0, "/home/fireworms/trading_system")

from app.core.database import SessionLocal
from app.models.stock_master import StockMaster
from app.services.kis.client import get_kis_client

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("sector"); log.setLevel(logging.INFO)


def main(fill_all: bool = False):
    db = SessionLocal()
    try:
        client = get_kis_client(db)
        q = db.query(StockMaster).filter(
            StockMaster.is_active == True,
            StockMaster.market.in_(["KOSPI", "KOSDAQ"]),
        )
        if not fill_all:
            q = q.filter((StockMaster.sector == None) | (StockMaster.sector == ""))  # noqa: E711
        rows = q.all()
        total = len(rows)
        log.info("백필 대상: %d종목 (fill_all=%s)", total, fill_all)

        ok = fail = 0
        for i, row in enumerate(rows, 1):
            try:
                info = client.get_stock_basic_info(row.stock_code)
                sector = (info or {}).get("sector") if info else None
                if sector:
                    row.sector = sector
                    ok += 1
                else:
                    fail += 1
            except Exception as e:
                fail += 1
                log.warning("실패 %s: %s", row.stock_code, e)
            if i % 200 == 0:
                db.commit()
                log.info("진행 %d/%d (ok=%d fail=%d)", i, total, ok, fail)
        db.commit()
        log.info("완료: ok=%d fail=%d / total=%d", ok, fail, total)
    finally:
        db.close()


if __name__ == "__main__":
    main(fill_all="--all" in sys.argv)

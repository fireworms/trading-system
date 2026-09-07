"""미확정 공시 후속 추적 pending 시드 (1회성 보정).

배경: 후속 추적(events._resolve_pending)은 새로 감지된 공시만 pending에 넣는다.
기능 도입 이전에 이미 seen으로 기록된 미확정 공시(예: 2026-09-04 SK하이닉스
"풍문 또는 보도에 대한 해명(미확정)")는 다시 감지되지 않아 후속을 놓친다.
관심종목 전체의 최근 공시를 훑어 미확정 건을 pending에 채운다. 멱등.

사용:
    python scripts/backfill_pending_disclosures.py [--days 60] [--dry-run]
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.core.config_store import set_config  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.watchlist import WatchlistStock  # noqa: E402
from app.services.dart.client import fetch_recent_disclosures  # noqa: E402
from app.services.watchlist.events import (  # noqa: E402
    _PENDING_DISCLOSURES_KEY, _disclosure_base, _kst_today, _load_json_config)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60, help="소급 조회 일수 (기본 60)")
    ap.add_argument("--dry-run", action="store_true", help="DB 미반영")
    args = ap.parse_args()

    today = _kst_today()
    with SessionLocal() as db:
        codes = sorted({(w.stock_code, w.stock_name)
                        for w in db.scalars(select(WatchlistStock)).all()})
        if not codes:
            print("관심종목 없음 — 종료")
            return
        pending = _load_json_config(db, _PENDING_DISCLOSURES_KEY)
        added = 0
        for code, name in codes:
            res = fetch_recent_disclosures(code, end_date=today, days=args.days)
            if not res.get("available"):
                print(f"  {name}({code}) 조회 실패 — {res.get('note')}")
                continue
            for it in res.get("items", []):
                title = it.get("title", "")
                no = it.get("rcept_no")
                if "미확정" not in title or not no or no in pending:
                    continue
                pending[no] = {"code": code, "title": title,
                               "base": _disclosure_base(title),
                               "date": it.get("date") or today.strftime("%Y%m%d")}
                added += 1
                print(f"  + {name}({code}) {title} ({it.get('date')})")
        print(f"\n미확정 {added}건 신규 추가, pending 총 {len(pending)}건")
        if args.dry_run:
            print("(dry-run — DB 미반영)")
            return
        set_config(db, _PENDING_DISCLOSURES_KEY, json.dumps(pending))
        db.commit()
        print("저장 완료 — 이후 16:30 잡이 후속 확정 공시를 자동 감지한다")


if __name__ == "__main__":
    main()

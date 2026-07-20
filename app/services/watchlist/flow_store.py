"""관심종목 일별 수급 적재 (investor_flow_daily).

KIS 종목별 투자자 API(FHKST01010900)는 최근 30거래일만 반환 — 60/120거래일
누적은 직접 조회가 불가하므로 매일 적재해 히스토리를 만든다.
백필 불가: 적재 시작일 이전 구간은 영원히 없음 → 커버리지를 항상 명시한다.
"""
import logging
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models.investor_flow import InvestorFlowDaily

logger = logging.getLogger(__name__)


def _dec(v) -> Decimal | None:
    return Decimal(str(round(v))) if v is not None else None


def upsert_investor_flows(db, stock_code: str, rows: list[dict]) -> int:
    """KIS get_investor_daily 응답을 적재. 이미 있는 (종목, 일자)는 최신 응답으로 갱신.

    do_nothing이 아닌 do_update인 이유: 장중 분석이 미확정(0) 행을 먼저 넣으면
    16:10 잡의 확정값이 영원히 못 덮어쓰는 동결 버그 (7/16·7/20 frgn=0 실사례).
    같은 소스의 최신 조회가 항상 더 확정된 값이다.
    """
    values = []
    for r in rows:
        d = r.get("date")
        if not d:
            continue
        values.append({
            "stock_code": stock_code,
            "trade_date": datetime.strptime(d, "%Y%m%d").date(),
            "frgn_ntby_amt": _dec(r.get("frgn_ntby_amt")),
            "orgn_ntby_amt": _dec(r.get("orgn_ntby_amt")),
            "prsn_ntby_amt": _dec(r.get("prsn_ntby_amt")),
            "close": _dec(r.get("close")),
        })
    if not values:
        return 0
    stmt = pg_insert(InvestorFlowDaily).values(values)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_invflow_stock_date",
        set_={
            "frgn_ntby_amt": stmt.excluded.frgn_ntby_amt,
            "orgn_ntby_amt": stmt.excluded.orgn_ntby_amt,
            "prsn_ntby_amt": stmt.excluded.prsn_ntby_amt,
            "close": stmt.excluded.close,
        },
    )
    result = db.execute(stmt)
    db.commit()
    return result.rowcount or 0


def get_extended_flow(db, stock_code: str) -> dict:
    """적재분 기반 60/120거래일 누적 (백만원).

    커버리지 미달 구간은 부분합으로 위장하지 않고 None — "60일 누적"이라는
    라벨에 40일치 합이 들어가면 매도 규모를 과소평가하게 됨.
    """
    rows = db.execute(
        select(InvestorFlowDaily)
        .where(InvestorFlowDaily.stock_code == stock_code)
        .order_by(InvestorFlowDaily.trade_date.desc())
        .limit(120)
    ).scalars().all()
    if not rows:
        return {"available": False,
                "note": "적재된 수급 이력 없음 — 이번 분석부터 축적 시작 (백필 불가)"}

    def _cum(attr: str, n: int) -> float | None:
        if len(rows) < n:
            return None
        vals = [getattr(r, attr) for r in rows[:n] if getattr(r, attr) is not None]
        return float(sum(vals)) if vals else None

    out = {
        "available": True,
        "unit": "백만원",
        "coverage_days": len(rows),
        "earliest_date": rows[-1].trade_date.isoformat(),
        "frgn_net_60d": _cum("frgn_ntby_amt", 60),
        "frgn_net_120d": _cum("frgn_ntby_amt", 120),
        "orgn_net_60d": _cum("orgn_ntby_amt", 60),
        "orgn_net_120d": _cum("orgn_ntby_amt", 120),
        "prsn_net_60d": _cum("prsn_ntby_amt", 60),
        "prsn_net_120d": _cum("prsn_ntby_amt", 120),
    }
    if len(rows) < 60:
        out["note"] = f"적재 {len(rows)}거래일분 — 60/120일 누적은 커버리지 도달 후 제공"
    elif len(rows) < 120:
        out["note"] = f"적재 {len(rows)}거래일분 — 120일 누적은 커버리지 도달 후 제공"
    return out


def collect_all_watchlist_flows() -> None:
    """스케줄러 잡 (16:10 평일): 전체 유저 관심종목의 일별 수급을 적재."""
    from app.core.database import SessionLocal
    from app.models.watchlist import WatchlistStock
    from app.services.kis.client import get_kis_client

    with SessionLocal() as db:
        codes = [c for (c,) in db.execute(
            select(WatchlistStock.stock_code).distinct()
        ).all()]
        if not codes:
            return
        client = get_kis_client(db)
        total = 0
        for code in codes:
            try:
                rows = client.get_investor_daily(code)
                total += upsert_investor_flows(db, code, rows)
            except Exception as e:
                logger.warning("flow collect failed for %s: %s", code, e)
        logger.info("Watchlist flow collect: %d codes, %d new rows", len(codes), total)

"""
stock_master 검색 API.
프론트엔드 자동완성용 + 관리자 수동 업데이트 트리거.
"""
from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.api.deps import get_db, get_current_user, require_admin
from app.models.stock_master import StockMaster
from app.models.user import User

router = APIRouter(prefix="/stock-master", tags=["stock-master"])


# ------------------------------------------------------------------ #
# 검색 (자동완성용)
# ------------------------------------------------------------------ #

@router.get("/search")
def search_stocks(
    q: str = Query(..., min_length=1, description="종목명 또는 코드 (부분 검색)"),
    market: Optional[str] = Query(None, description="KOSPI/KOSDAQ/NAS"),
    limit: int = Query(15, ge=1, le=50),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """종목명/코드 부분 검색. 자동완성 드롭다운용."""
    qry = db.query(StockMaster).filter(StockMaster.is_active == True)

    if market:
        qry = qry.filter(StockMaster.market == market.upper())

    q = q.strip()
    qry = qry.filter(
        or_(
            StockMaster.stock_code.ilike(f"{q}%"),
            StockMaster.stock_name.ilike(f"%{q}%"),
        )
    )

    rows = qry.order_by(StockMaster.stock_code).limit(limit).all()
    return [
        {
            "stock_code": r.stock_code,
            "stock_name": r.stock_name,
            "market":     r.market,
            "country":    r.country,
            "sector":     r.sector,
        }
        for r in rows
    ]


# ------------------------------------------------------------------ #
# 통계
# ------------------------------------------------------------------ #

@router.get("/stats")
def get_stats(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    from sqlalchemy import func
    rows = (
        db.query(StockMaster.market, func.count(StockMaster.stock_id))
        .filter(StockMaster.is_active == True)
        .group_by(StockMaster.market)
        .all()
    )
    return {r[0]: r[1] for r in rows}


# ------------------------------------------------------------------ #
# 관리자: 수동 업데이트 트리거
# ------------------------------------------------------------------ #

@router.post("/update", status_code=202)
def trigger_update(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """stock_master MST 파일 다운로드 및 업데이트 (백그라운드)."""
    from fastapi.concurrency import run_in_threadpool
    import asyncio
    from app.services.stock_master.updater import update_stock_master

    # 동기 함수이므로 스레드에서 실행
    from app.core.database import SessionLocal
    import threading

    def _run():
        with SessionLocal() as s:
            result = update_stock_master(s)
            import logging
            logging.getLogger(__name__).info("Manual stock_master update: %s", result)

    threading.Thread(target=_run, daemon=True).start()
    return {"status": "started"}



"""관심종목 분석 탭 API (중장기 수동매매 일지). 스펙: docs/watchlist_spec.md"""
import uuid
from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select, func

from app.core.database import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.models.stock_master import StockMaster
from app.models.watchlist import WatchlistStock, StockAnalysis
from app.schemas.watchlist import (
    WatchlistCreate, WatchlistUpdate, WatchlistOut,
    AnalyzeRequest, AnalysisSummaryOut, AnalysisDetailOut,
)

router = APIRouter(prefix="/watchlist", tags=["watchlist"])


def _resolve_stock(db: Session, stock_code: str) -> tuple[str, str | None]:
    """종목명/섹터 확인: stock_master 우선, 없으면 KIS 기본정보 조회."""
    row = db.scalar(
        select(StockMaster).where(
            StockMaster.stock_code == stock_code,
            StockMaster.is_active == True,  # noqa: E712
        ).limit(1)
    )
    if row:
        return row.stock_name, row.sector

    from app.services.kis.client import get_kis_client
    info = get_kis_client(db).get_stock_basic_info(stock_code)
    if not info:
        raise HTTPException(status_code=404, detail=f"종목 {stock_code}를 찾을 수 없습니다.")
    return info["stock_name"], info.get("sector") or None


def _to_out(w: WatchlistStock, count: int, last_date: date | None) -> WatchlistOut:
    out = WatchlistOut.model_validate(w)
    out.analysis_count = count
    out.last_analysis_date = last_date
    return out


# ------------------------------------------------------------------ #
# 관심종목 CRUD
# ------------------------------------------------------------------ #

@router.post("", response_model=WatchlistOut, status_code=201)
def add_stock(
    body: WatchlistCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    dup = db.scalar(select(WatchlistStock).where(
        WatchlistStock.user_id == current_user.user_id,
        WatchlistStock.stock_code == body.stock_code,
    ))
    if dup:
        raise HTTPException(status_code=409, detail="이미 관심종목에 있습니다.")

    name, sector = _resolve_stock(db, body.stock_code)
    watch = WatchlistStock(
        user_id=current_user.user_id,
        stock_code=body.stock_code,
        stock_name=name,
        sector=sector,
        memo=body.memo,
    )
    db.add(watch)
    db.commit()
    db.refresh(watch)
    return _to_out(watch, 0, None)


@router.get("", response_model=list[WatchlistOut])
def list_watchlist(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    watches = db.scalars(
        select(WatchlistStock)
        .where(WatchlistStock.user_id == current_user.user_id)
        .order_by(WatchlistStock.added_at.desc())
    ).all()

    # 종목별 분석 횟수/최근 분석일 (일지 이력은 관심종목 삭제 후에도 유지되므로 별도 집계)
    stats = {
        code: (cnt, last)
        for code, cnt, last in db.execute(
            select(
                StockAnalysis.stock_code,
                func.count(StockAnalysis.analysis_id),
                func.max(StockAnalysis.analysis_date),
            )
            .where(StockAnalysis.user_id == current_user.user_id)
            .group_by(StockAnalysis.stock_code)
        ).all()
    }
    return [
        _to_out(w, *stats.get(w.stock_code, (0, None)))
        for w in watches
    ]


@router.patch("/{watch_id}", response_model=WatchlistOut)
def update_stock(
    watch_id: uuid.UUID,
    body: WatchlistUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    watch = db.get(WatchlistStock, watch_id)
    if not watch or watch.user_id != current_user.user_id:
        raise HTTPException(status_code=404, detail="관심종목을 찾을 수 없습니다.")
    if body.memo is not None:
        watch.memo = body.memo
    db.commit()
    db.refresh(watch)

    cnt, last = db.execute(
        select(func.count(StockAnalysis.analysis_id), func.max(StockAnalysis.analysis_date))
        .where(StockAnalysis.user_id == current_user.user_id,
               StockAnalysis.stock_code == watch.stock_code)
    ).one()
    return _to_out(watch, cnt, last)


@router.delete("/{watch_id}", status_code=204)
def remove_stock(
    watch_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    watch = db.get(WatchlistStock, watch_id)
    if not watch or watch.user_id != current_user.user_id:
        raise HTTPException(status_code=404, detail="관심종목을 찾을 수 없습니다.")
    db.delete(watch)  # 분석 일지(stock_analyses)는 FK가 없어 그대로 보존됨
    db.commit()


# ------------------------------------------------------------------ #
# 분석 실행 / 이력 조회
# ------------------------------------------------------------------ #

@router.post("/analyze", response_model=AnalysisDetailOut)
def analyze(
    body: AnalyzeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    watch = db.scalar(select(WatchlistStock).where(
        WatchlistStock.user_id == current_user.user_id,
        WatchlistStock.stock_code == body.stock_code,
    ))
    if not watch:
        raise HTTPException(status_code=404, detail="관심종목에 먼저 추가해주세요.")

    from app.services.watchlist.analyzer import run_analysis
    try:
        analysis = run_analysis(
            db,
            user_id=current_user.user_id,
            stock_code=watch.stock_code,
            stock_name=watch.stock_name,
            sector=watch.sector,
            analysis_date=body.analysis_date or date.today(),
            trigger_type=body.trigger_type,
        )
    except ValueError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=f"AI 분석 실패: {e}")
    return analysis


@router.get("/analyses/{stock_code}", response_model=list[AnalysisSummaryOut])
def list_analyses(
    stock_code: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return db.scalars(
        select(StockAnalysis)
        .where(StockAnalysis.user_id == current_user.user_id,
               StockAnalysis.stock_code == stock_code)
        .order_by(StockAnalysis.analysis_date.desc(), StockAnalysis.created_at.desc())
    ).all()


@router.get("/analysis/{analysis_id}", response_model=AnalysisDetailOut)
def get_analysis(
    analysis_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    analysis = db.get(StockAnalysis, analysis_id)
    if not analysis or analysis.user_id != current_user.user_id:
        raise HTTPException(status_code=404, detail="분석을 찾을 수 없습니다.")
    return analysis

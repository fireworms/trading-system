"""
후보 종목 풀 관리 API.
관리자만 추가/수정/삭제, 일반 유저는 조회만.
"""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_current_user, require_admin
from app.models.candidate_stock import CandidateStock
from app.models.user import User

router = APIRouter(prefix="/candidate-stocks", tags=["candidate-stocks"])


# ------------------------------------------------------------------ #
# Schemas
# ------------------------------------------------------------------ #

class CandidateStockOut(BaseModel):
    stock_id: int
    stock_code: str
    stock_name: str
    market: Optional[str]
    sector: Optional[str]
    is_active: bool
    notes: Optional[str]

    class Config:
        from_attributes = True


class CandidateStockCreate(BaseModel):
    stock_code: str
    stock_name: str
    market: Optional[str] = None
    sector: Optional[str] = None
    notes: Optional[str] = None


class CandidateStockUpdate(BaseModel):
    stock_name: Optional[str] = None
    market: Optional[str] = None
    sector: Optional[str] = None
    is_active: Optional[bool] = None
    notes: Optional[str] = None


class BulkImportItem(BaseModel):
    stock_code: str
    stock_name: str
    market: Optional[str] = None
    sector: Optional[str] = None


# ------------------------------------------------------------------ #
# Endpoints
# ------------------------------------------------------------------ #

@router.get("", response_model=list[CandidateStockOut])
def list_candidates(
    active_only: bool = Query(True, description="활성 종목만 조회"),
    market: Optional[str] = Query(None, description="KOSPI / KOSDAQ 필터"),
    sector: Optional[str] = Query(None, description="섹터 필터 (부분 일치)"),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    q = db.query(CandidateStock)
    if active_only:
        q = q.filter(CandidateStock.is_active == True)
    if market:
        q = q.filter(CandidateStock.market == market.upper())
    if sector:
        q = q.filter(CandidateStock.sector.ilike(f"%{sector}%"))
    return q.order_by(CandidateStock.stock_id).all()


@router.post("", response_model=CandidateStockOut, status_code=status.HTTP_201_CREATED)
def create_candidate(
    body: CandidateStockCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    existing = db.query(CandidateStock).filter(
        CandidateStock.stock_code == body.stock_code
    ).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"stock_code '{body.stock_code}' already exists",
        )
    stock = CandidateStock(**body.model_dump())
    db.add(stock)
    db.commit()
    db.refresh(stock)
    return stock


@router.patch("/{stock_id}", response_model=CandidateStockOut)
def update_candidate(
    stock_id: int,
    body: CandidateStockUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    stock = db.get(CandidateStock, stock_id)
    if not stock:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(stock, field, value)
    db.commit()
    db.refresh(stock)
    return stock


@router.delete("/{stock_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_candidate(
    stock_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    stock = db.get(CandidateStock, stock_id)
    if not stock:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    db.delete(stock)
    db.commit()


@router.post("/bulk-import", response_model=dict)
def bulk_import(
    items: list[BulkImportItem],
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """여러 종목 일괄 등록. 이미 존재하는 code는 건너뜀."""
    existing_codes = {
        r[0] for r in db.query(CandidateStock.stock_code).all()
    }
    new_stocks = [
        CandidateStock(**item.model_dump())
        for item in items
        if item.stock_code not in existing_codes
    ]
    db.add_all(new_stocks)
    db.commit()
    return {"created": len(new_stocks), "skipped": len(items) - len(new_stocks)}

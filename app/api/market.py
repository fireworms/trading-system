from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from decimal import Decimal
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.core.database import get_db
from app.models.user import User, BrokerAccount
from app.services.kis.client import get_kis_client, get_kis_client_from_account, OHLCVBar, BalanceItem
from app.api.deps import get_current_user

router = APIRouter(prefix="/market", tags=["market"])


class StockInfoOut(BaseModel):
    stock_code: str
    current_price: int
    rsi_14: float | None
    ma5: int | None
    ma20: int | None
    ma60: int | None
    avg_volume_20d: int
    frgn_net_buy_1d: int
    frgn_net_buy_5d: int
    orgn_net_buy_1d: int
    orgn_net_buy_5d: int
    recent_ohlcv: list[dict]


class OHLCVOut(BaseModel):
    date: str
    open: int
    high: int
    low: int
    close: int
    volume: int


class BalanceItemOut(BaseModel):
    stock_code: str
    stock_name: str
    quantity: int
    avg_price: Decimal
    current_price: Decimal
    pnl_pct: Decimal


def _user_account(user: User, db: Session) -> BrokerAccount:
    """현재 유저의 첫 번째 활성 계좌 반환."""
    account = db.scalar(
        select(BrokerAccount)
        .where(BrokerAccount.user_id == user.user_id)
        .where(BrokerAccount.is_active == True)  # noqa: E712
        .limit(1)
    )
    if not account:
        raise HTTPException(status_code=404, detail="등록된 활성 broker_account가 없습니다.")
    return account


# ------------------------------------------------------------------ #
# 시장 데이터 (any active account)
# ------------------------------------------------------------------ #

@router.get("/price/{stock_code}")
def get_price(
    stock_code: str,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    try:
        return {"stock_code": stock_code, "current_price": int(get_kis_client(db).get_current_price(stock_code))}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"KIS API error: {e}")


@router.get("/stock/{stock_code}", response_model=StockInfoOut)
def get_stock_info(
    stock_code: str,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    try:
        return get_kis_client(db).get_stock_info(stock_code)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"KIS API error: {e}")


@router.get("/ohlcv/{stock_code}", response_model=list[OHLCVOut])
def get_ohlcv(
    stock_code: str,
    days: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    try:
        bars = get_kis_client(db).get_ohlcv(stock_code, days)
        return [
            {"date": b.date, "open": int(b.open), "high": int(b.high),
             "low": int(b.low), "close": int(b.close), "volume": b.volume}
            for b in bars
        ]
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"KIS API error: {e}")


# ------------------------------------------------------------------ #
# 계좌 데이터 (current user's account)
# ------------------------------------------------------------------ #

@router.get("/balance", response_model=list[BalanceItemOut])
def get_balance(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        client = get_kis_client_from_account(_user_account(current_user, db))
        return client.get_balance()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"KIS API error: {e}")


@router.get("/buyable-cash")
def get_buyable_cash(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        client = get_kis_client_from_account(_user_account(current_user, db))
        return {"buyable_cash": int(client.get_buyable_cash())}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"KIS API error: {e}")

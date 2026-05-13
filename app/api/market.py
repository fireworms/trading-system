import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from decimal import Decimal
from sqlalchemy.orm import Session
from sqlalchemy import select

logger = logging.getLogger(__name__)

from app.core.database import get_db
from app.models.user import User, BrokerAccount
from app.services.kis.client import get_kis_client, get_kis_client_from_account, OHLCVBar, BalanceItem
from app.api.deps import get_current_user

router = APIRouter(prefix="/market", tags=["market"])

# ------------------------------------------------------------------ #
# 시장 현황 캐시 + 대표 종목 목록
# ------------------------------------------------------------------ #
_overview_cache: dict | None = None
_overview_cache_ts: float = 0.0
_OVERVIEW_TTL = 60.0

_KOSPI_STOCKS = [
    ("005930", "삼성전자"), ("000660", "SK하이닉스"), ("373220", "LG에너지솔루션"), ("005380", "현대차"),
    ("005490", "POSCO홀딩스"), ("207940", "삼성바이오"), ("105560", "KB금융"), ("000270", "기아"),
]
_KOSDAQ_STOCKS = [
    ("247540", "에코프로비엠"), ("028300", "HLB"), ("196170", "알테오젠"),
    ("141080", "리가켐바이오"), ("403870", "HPSP"), ("277810", "레인보우로보틱스"),
]
_NAS_STOCKS = [
    ("NVDA", "NVIDIA"), ("AAPL", "Apple"), ("MSFT", "Microsoft"), ("AMZN", "Amazon"),
    ("META", "Meta"), ("GOOGL", "Alphabet"), ("TSLA", "Tesla"), ("AVGO", "Broadcom"),
]
_QQQ = ("QQQ", "NASDAQ-100 ETF")


class StockInfoOut(BaseModel):
    stock_code: str
    currency: str = "KRW"
    current_price: float
    rsi_14: float | None
    ma5: float | None
    ma20: float | None
    ma60: float | None
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


class IndexInfo(BaseModel):
    level: float
    change_pct: float


class StockSnap(BaseModel):
    code: str
    name: str
    price: float
    change_pct: float


class MarketOverviewOut(BaseModel):
    kospi: IndexInfo
    kosdaq: IndexInfo
    nasdaq: IndexInfo
    kospi_stocks: list[StockSnap]
    kosdaq_stocks: list[StockSnap]
    nasdaq_stocks: list[StockSnap]
    cached_at: float


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
# 종목 기본정보 조회 (코드 → 이름/시장/섹터)
# ------------------------------------------------------------------ #

@router.get("/stock-basic/{stock_code}")
def get_stock_basic(
    stock_code: str,
    country: str = Query("KR", description="KR 또는 US"),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """종목코드로 종목명/시장/섹터 조회."""
    if country.upper() == "US":
        from app.models.stock_master import StockMaster
        row = db.scalar(
            select(StockMaster).where(
                StockMaster.stock_code == stock_code.upper(),
                StockMaster.country == "US",
            )
        )
        if not row:
            raise HTTPException(status_code=404, detail="종목 정보를 찾을 수 없습니다.")
        return {
            "stock_code": row.stock_code,
            "stock_name": row.stock_name,
            "market": row.market,
            "sector": row.sector,
            "country": "US",
        }

    info = get_kis_client(db).get_stock_basic_info(stock_code)
    if not info:
        raise HTTPException(status_code=404, detail="종목 정보를 찾을 수 없습니다.")
    return {**info, "country": "KR"}


# ------------------------------------------------------------------ #
# 시장 데이터 (any active account)
# ------------------------------------------------------------------ #

@router.get("/price/{stock_code}")
def get_price(
    stock_code: str,
    country: str = Query("KR", description="KR / US"),
    market: str | None = Query(None, description="US 거래소: NAS / NYS / AMS"),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    try:
        client = get_kis_client(db)
        if country.upper() == "US":
            exchange = (market or "NAS").upper()
            price = client.get_us_current_price(stock_code, exchange)
            return {"stock_code": stock_code, "currency": "USD", "current_price": float(price)}
        data = client._get(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            "FHKST01010100",
            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": stock_code},
        )
        o = data.get("output", {})
        price = int(o.get("stck_prpr") or 0)
        change_pct = float(o.get("prdy_ctrt") or 0)
        if abs(change_pct) > 100:
            vrss = float(o.get("prdy_vrss") or 0)
            sign = 1 if o.get("prdy_vrss_sign", "3") in ("1", "2") else (-1 if o.get("prdy_vrss_sign") in ("4", "5") else 0)
            prev_close = price - vrss * sign
            change_pct = round(vrss * sign / prev_close * 100, 2) if prev_close else 0.0
        return {
            "stock_code": stock_code,
            "currency": "KRW",
            "current_price": price,
            "open_price": int(o.get("stck_oprc") or 0),
            "change_pct": change_pct,
        }
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"KIS API error: {e}")


@router.get("/stock/{stock_code}", response_model=StockInfoOut)
def get_stock_info(
    stock_code: str,
    country: str = Query("KR", description="KR / US"),
    market: str | None = Query(None, description="US 거래소: NAS / NYS / AMS"),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    try:
        return get_kis_client(db).get_stock_info(stock_code, country=country, market=market)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"KIS API error: {e}")


@router.get("/ohlcv/{stock_code}", response_model=list[OHLCVOut])
def get_ohlcv(
    stock_code: str,
    days: int = Query(30, ge=1, le=100),
    country: str = Query("KR", description="KR / US"),
    market: str | None = Query(None, description="US 거래소: NAS / NYS / AMS"),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    try:
        client = get_kis_client(db)
        if country.upper() == "US":
            exchange = (market or "NAS").upper()
            bars = client.get_us_ohlcv(stock_code, exchange, days)
        else:
            bars = client.get_ohlcv(stock_code, days)
        return [
            {"date": b.date, "open": float(b.open), "high": float(b.high),
             "low": float(b.low), "close": float(b.close), "volume": b.volume}
            for b in bars
        ]
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"KIS API error: {e}")


# ------------------------------------------------------------------ #
# 시장 현황 스냅샷 (60초 캐시)
# ------------------------------------------------------------------ #

@router.get("/overview", response_model=MarketOverviewOut)
def get_market_overview(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """KOSPI/KOSDAQ 지수 + 대표 종목 현재가 스냅샷 (60초 캐시)."""
    global _overview_cache, _overview_cache_ts

    now = time.monotonic()
    if _overview_cache is not None and (now - _overview_cache_ts) < _OVERVIEW_TTL:
        return _overview_cache

    client = get_kis_client(db)

    def _qqq_as_index() -> dict:
        r = client.get_us_price_with_change(_QQQ[0], "NAS")
        return {"level": r["price"], "change_pct": r["change_pct"]}

    tasks: list[tuple[str, object]] = [
        ("idx_kospi",  lambda: client.get_index_overview("0001")),
        ("idx_kosdaq", lambda: client.get_index_overview("1001")),
        ("idx_nasdaq", _qqq_as_index),
        *[(f"kospi_{c}",  lambda c=c: client.get_price_with_change(c))       for c, _ in _KOSPI_STOCKS],
        *[(f"kosdaq_{c}", lambda c=c: client.get_price_with_change(c))       for c, _ in _KOSDAQ_STOCKS],
        *[(f"nas_{c}",    lambda c=c: client.get_us_price_with_change(c, "NAS")) for c, _ in _NAS_STOCKS],
    ]

    results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        future_to_key = {pool.submit(fn): key for key, fn in tasks}
        for future in as_completed(future_to_key):
            key = future_to_key[future]
            try:
                results[key] = future.result()
            except Exception as e:
                logger.warning("overview task %s failed: %s", key, e)
                results[key] = {}

    def _snap(code: str, name: str, prefix: str) -> StockSnap:
        r = results.get(f"{prefix}_{code}", {})
        return StockSnap(code=code, name=name, price=r.get("price", 0), change_pct=r.get("change_pct", 0.0))

    payload = MarketOverviewOut(
        kospi=IndexInfo(**results.get("idx_kospi",  {"level": 0.0, "change_pct": 0.0})),
        kosdaq=IndexInfo(**results.get("idx_kosdaq", {"level": 0.0, "change_pct": 0.0})),
        nasdaq=IndexInfo(**results.get("idx_nasdaq", {"level": 0.0, "change_pct": 0.0})),
        kospi_stocks =[_snap(c, n, "kospi")  for c, n in _KOSPI_STOCKS],
        kosdaq_stocks=[_snap(c, n, "kosdaq") for c, n in _KOSDAQ_STOCKS],
        nasdaq_stocks=[_snap(c, n, "nas")    for c, n in _NAS_STOCKS],
        cached_at=time.time(),
    )
    _overview_cache = payload
    _overview_cache_ts = now
    return payload


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

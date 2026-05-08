"""
KIS(한국투자증권) OpenAPI 네이티브 클라이언트.
httpx로 REST API 직접 호출. pykis 의존성 없음.

계좌번호 형식: "00000000-01" → CANO="00000000", ACNT_PRDT_CD="01"
토큰: 인스턴스당 1회 발급, 만료 5분 전 자동 갱신
"""
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from dataclasses import dataclass

import json
import tempfile
from pathlib import Path

import httpx

from app.core.config import get_settings
from app.core.security import decrypt_secret

_TOKEN_CACHE_PATH = Path(tempfile.gettempdir()) / "kis_token_cache.json"

logger = logging.getLogger(__name__)

_BASE_REAL    = "https://openapi.koreainvestment.com:9443"
_BASE_VIRTUAL = "https://openapivts.koreainvestment.com:29443"


# ------------------------------------------------------------------ #
# 데이터 클래스
# ------------------------------------------------------------------ #

@dataclass(frozen=True)
class OHLCVBar:
    date: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


@dataclass(frozen=True)
class BalanceItem:
    stock_code: str
    stock_name: str
    quantity: int
    avg_price: Decimal
    current_price: Decimal
    pnl_pct: Decimal


# ------------------------------------------------------------------ #
# KISClient
# ------------------------------------------------------------------ #

class KISClient:
    """KIS OpenAPI 클라이언트 (국내 주식 전용)."""

    def __init__(self, app_key: str, app_secret: str, account_no: str, is_real: bool = True):
        self._key    = app_key
        self._secret = app_secret
        parts = account_no.split("-", 1)
        self._cano       = parts[0]
        self._acnt_prdt  = parts[1] if len(parts) > 1 else "01"
        self._is_real    = is_real
        self._base       = _BASE_REAL if is_real else _BASE_VIRTUAL
        self._token: str | None = None
        self._token_exp: datetime | None = None

    # ------------------------------------------------------------------ #
    # 인증
    # ------------------------------------------------------------------ #

    def _ensure_token(self) -> None:
        # 1) 인메모리 캐시 확인
        if self._token and self._token_exp and datetime.now() < self._token_exp:
            return

        # 2) 파일 캐시 확인 (프로세스 간 토큰 공유)
        cached = self._load_token_cache()
        if cached:
            self._token     = cached["token"]
            self._token_exp = datetime.fromisoformat(cached["exp"])
            return

        # 3) 신규 발급
        resp = httpx.post(
            f"{self._base}/oauth2/tokenP",
            json={
                "grant_type": "client_credentials",
                "appkey": self._key,
                "appsecret": self._secret,
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if "access_token" not in data:
            raise RuntimeError(f"KIS 토큰 발급 실패: {data}")
        expires_in      = int(data.get("expires_in", 86400))
        self._token     = f"Bearer {data['access_token']}"
        self._token_exp = datetime.now() + timedelta(seconds=expires_in - 300)
        self._save_token_cache()
        logger.info("KIS 토큰 발급 완료 (유효 %ds)", expires_in)

    def _load_token_cache(self) -> dict | None:
        try:
            if not _TOKEN_CACHE_PATH.exists():
                return None
            raw = json.loads(_TOKEN_CACHE_PATH.read_text())
            exp = datetime.fromisoformat(raw["exp"])
            if datetime.now() >= exp:
                return None
            return raw
        except Exception:
            return None

    def _save_token_cache(self) -> None:
        try:
            _TOKEN_CACHE_PATH.write_text(json.dumps({
                "token": self._token,
                "exp":   self._token_exp.isoformat(),
            }))
        except Exception as e:
            logger.warning("토큰 캐시 저장 실패: %s", e)

    def _headers(self, tr_id: str) -> dict:
        self._ensure_token()
        return {
            "authorization": self._token,
            "appkey":        self._key,
            "appsecret":     self._secret,
            "tr_id":         tr_id,
            "content-type":  "application/json; charset=utf-8",
        }

    # ------------------------------------------------------------------ #
    # HTTP 헬퍼
    # ------------------------------------------------------------------ #

    def _get(self, path: str, tr_id: str, params: dict) -> dict:
        resp = httpx.get(
            f"{self._base}{path}",
            headers=self._headers(tr_id),
            params=params,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("rt_cd") not in ("0", None):
            raise RuntimeError(f"KIS API 오류 [{tr_id}]: {data.get('msg1', data)}")
        return data

    def _post(self, path: str, tr_id: str, body: dict) -> dict:
        hash_key = self._hash_key(body)
        headers  = self._headers(tr_id)
        headers["hashkey"] = hash_key
        resp = httpx.post(
            f"{self._base}{path}",
            headers=headers,
            json=body,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("rt_cd") not in ("0", None):
            raise RuntimeError(f"KIS API 오류 [{tr_id}]: {data.get('msg1', data)}")
        return data

    def _hash_key(self, body: dict) -> str:
        resp = httpx.post(
            f"{self._base}/uapi/hashkey",
            headers={
                "appkey":       self._key,
                "appsecret":    self._secret,
                "content-type": "application/json",
            },
            json=body,
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get("HASH", "")

    # ------------------------------------------------------------------ #
    # 시세 조회
    # ------------------------------------------------------------------ #

    def get_current_price(self, stock_code: str) -> Decimal:
        """현재가 조회."""
        data = self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            "FHKST01010100",
            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": stock_code},
        )
        return Decimal(data["output"]["stck_prpr"])

    def get_ohlcv(self, stock_code: str, days: int = 100) -> list[OHLCVBar]:
        """
        일봉 OHLCV 조회 (최신 → 오래된 순).
        inquire-daily-itemchartprice 사용 → 최대 100거래일 지원.
        """
        today = date.today().strftime("%Y%m%d")
        start = (date.today() - timedelta(days=days + 60)).strftime("%Y%m%d")
        data = self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            "FHKST03010100",
            {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD":         stock_code,
                "FID_INPUT_DATE_1":       start,
                "FID_INPUT_DATE_2":       today,
                "FID_PERIOD_DIV_CODE":    "D",
                "FID_ORG_ADJ_PRC":        "0",
            },
        )
        bars = []
        for item in data.get("output2", []):
            close = item.get("stck_clpr", "0")
            if not close or close == "0":
                continue
            bars.append(OHLCVBar(
                date=item["stck_bsop_date"],
                open=Decimal(item.get("stck_oprc") or close),
                high=Decimal(item.get("stck_hgpr") or close),
                low=Decimal(item.get("stck_lwpr") or close),
                close=Decimal(close),
                volume=int(item.get("acml_vol") or 0),
            ))
        # output2는 최신→오래된 순으로 이미 정렬되어 있음
        return bars[:days]

    def get_investor_trend(self, stock_code: str) -> dict:
        """
        외국인/기관 순매수 동향 (최근 30거래일).
        반환: 1일/5일 외국인·기관 순매수 수량 합계
        """
        try:
            data = self._get(
                "/uapi/domestic-stock/v1/quotations/inquire-investor",
                "FHKST01010900",
                {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": stock_code},
            )
        except Exception as e:
            logger.warning("investor_trend failed for %s: %s", stock_code, e)
            return {"frgn_net_buy_1d": 0, "frgn_net_buy_5d": 0,
                    "orgn_net_buy_1d": 0, "orgn_net_buy_5d": 0}

        def _int(v) -> int:
            try:
                return int(v or 0)
            except (ValueError, TypeError):
                return 0

        rows = data.get("output", [])
        if not rows:
            return {"frgn_net_buy_1d": 0, "frgn_net_buy_5d": 0,
                    "orgn_net_buy_1d": 0, "orgn_net_buy_5d": 0}

        return {
            "frgn_net_buy_1d": _int(rows[0].get("frgn_ntby_qty")),
            "frgn_net_buy_5d": sum(_int(r.get("frgn_ntby_qty")) for r in rows[:5]),
            "orgn_net_buy_1d": _int(rows[0].get("orgn_ntby_qty")),
            "orgn_net_buy_5d": sum(_int(r.get("orgn_ntby_qty")) for r in rows[:5]),
        }

    # ------------------------------------------------------------------ #
    # 기술적 지표 계산 (내부 헬퍼)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _compute_rsi(bars: list[OHLCVBar], period: int = 14) -> Decimal | None:
        if len(bars) < period + 1:
            return None
        closes = [float(b.close) for b in reversed(bars)]
        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        gains  = [d if d > 0 else 0.0 for d in deltas]
        losses = [-d if d < 0 else 0.0 for d in deltas]
        avg_g  = sum(gains[:period]) / period
        avg_l  = sum(losses[:period]) / period
        for i in range(period, len(deltas)):
            avg_g = (avg_g * (period - 1) + gains[i]) / period
            avg_l = (avg_l * (period - 1) + losses[i]) / period
        if avg_l == 0:
            return Decimal("100")
        return Decimal(str(round(100 - 100 / (1 + avg_g / avg_l), 2)))

    @staticmethod
    def _compute_mas(bars: list[OHLCVBar]) -> dict[str, Decimal | None]:
        closes = [float(b.close) for b in reversed(bars)]

        def ma(n: int) -> Decimal | None:
            if len(closes) < n:
                return None
            return Decimal(str(round(sum(closes[-n:]) / n, 0)))

        return {"ma5": ma(5), "ma20": ma(20), "ma60": ma(min(60, len(closes)))}

    # ------------------------------------------------------------------ #
    # 통합 종목 정보 (Gemini Stage4 입력용)
    # ------------------------------------------------------------------ #

    def get_stock_info(self, stock_code: str) -> dict:
        """현재가 + OHLCV + 기술적지표 + 외국인/기관 순매수 통합 조회."""
        current_price = self.get_current_price(stock_code)
        bars          = self.get_ohlcv(stock_code)
        rsi           = self._compute_rsi(bars)
        mas           = self._compute_mas(bars)
        investor      = self.get_investor_trend(stock_code)

        recent     = bars[:5] if bars else []
        avg_volume = int(sum(b.volume for b in bars[:20]) / min(20, len(bars))) if bars else 0

        return {
            "stock_code":     stock_code,
            "current_price":  int(current_price),
            "rsi_14":         float(rsi) if rsi else None,
            "ma5":            int(mas["ma5"])  if mas["ma5"]  else None,
            "ma20":           int(mas["ma20"]) if mas["ma20"] else None,
            "ma60":           int(mas["ma60"]) if mas["ma60"] else None,
            "avg_volume_20d": avg_volume,
            # 외국인/기관 순매수 (수량, 양수=순매수 / 음수=순매도)
            "frgn_net_buy_1d": investor["frgn_net_buy_1d"],
            "frgn_net_buy_5d": investor["frgn_net_buy_5d"],
            "orgn_net_buy_1d": investor["orgn_net_buy_1d"],
            "orgn_net_buy_5d": investor["orgn_net_buy_5d"],
            "recent_ohlcv": [
                {
                    "date":   b.date,
                    "open":   int(b.open),
                    "high":   int(b.high),
                    "low":    int(b.low),
                    "close":  int(b.close),
                    "volume": b.volume,
                }
                for b in recent
            ],
        }

    # ------------------------------------------------------------------ #
    # 계좌 조회
    # ------------------------------------------------------------------ #

    def _balance_raw(self) -> dict:
        return self._get(
            "/uapi/domestic-stock/v1/trading/inquire-balance",
            "TTTC8434R" if self._is_real else "VTTC8434R",
            {
                "CANO":                  self._cano,
                "ACNT_PRDT_CD":          self._acnt_prdt,
                "AFHR_FLPR_YN":          "N",
                "OFL_YN":                "",
                "INQR_DVSN":             "02",
                "UNPR_DVSN":             "01",
                "FUND_STTL_ICLD_YN":     "N",
                "FNCG_AMT_AUTO_RDPT_YN": "N",
                "PRCS_DVSN":             "01",
                "CTX_AREA_FK100":        "",
                "CTX_AREA_NK100":        "",
            },
        )

    def get_balance(self) -> list[BalanceItem]:
        """보유 주식 잔고 조회."""
        data  = self._balance_raw()
        items = []
        for row in data.get("output1", []):
            qty = int(row.get("hldg_qty") or 0)
            if qty == 0:
                continue
            items.append(BalanceItem(
                stock_code=    row.get("pdno", ""),
                stock_name=    row.get("prdt_name", ""),
                quantity=      qty,
                avg_price=     Decimal(row.get("pchs_avg_pric") or "0"),
                current_price= Decimal(row.get("prpr") or "0"),
                pnl_pct=       Decimal(row.get("evlu_pfls_rt") or "0"),
            ))
        return items

    def get_buyable_cash(self) -> Decimal:
        """매수 가능 예수금 조회."""
        data    = self._balance_raw()
        output2 = data.get("output2", [])
        if output2:
            return Decimal(output2[0].get("dnca_tot_amt") or "0")
        return Decimal("0")

    # ------------------------------------------------------------------ #
    # 주문
    # ------------------------------------------------------------------ #

    def buy_market_order(self, stock_code: str, quantity: int) -> dict:
        """시장가 매수."""
        tr_id = "TTTC0802U" if self._is_real else "VTTC0802U"
        return self._post(
            "/uapi/domestic-stock/v1/trading/order-cash",
            tr_id,
            {
                "CANO":         self._cano,
                "ACNT_PRDT_CD": self._acnt_prdt,
                "PDNO":         stock_code,
                "ORD_DVSN":     "01",
                "ORD_QTY":      str(quantity),
                "ORD_UNPR":     "0",
            },
        )

    def sell_market_order(self, stock_code: str, quantity: int) -> dict:
        """시장가 매도."""
        tr_id = "TTTC0801U" if self._is_real else "VTTC0801U"
        return self._post(
            "/uapi/domestic-stock/v1/trading/order-cash",
            tr_id,
            {
                "CANO":         self._cano,
                "ACNT_PRDT_CD": self._acnt_prdt,
                "PDNO":         stock_code,
                "ORD_DVSN":     "01",
                "ORD_QTY":      str(quantity),
                "ORD_UNPR":     "0",
            },
        )


# ------------------------------------------------------------------ #
# 팩토리
# ------------------------------------------------------------------ #

def get_kis_client(db=None) -> "KISClient":
    """
    시장 데이터용 클라이언트.
    DB의 첫 번째 활성 broker_account를 사용한다.
    db를 넘기지 않으면 내부에서 새 세션을 생성한다.
    """
    if db is not None:
        return _client_from_db(db)

    from app.core.database import SessionLocal
    with SessionLocal() as sess:
        return _client_from_db(sess)


def _client_from_db(db) -> "KISClient":
    from sqlalchemy import select
    from app.models.user import BrokerAccount

    account = db.scalar(
        select(BrokerAccount)
        .where(BrokerAccount.is_active == True)  # noqa: E712
        .limit(1)
    )
    if not account:
        raise RuntimeError("활성 broker_account가 없습니다. DB에 계좌를 먼저 등록하세요.")
    return get_kis_client_from_account(account)


def get_kis_client_from_account(account) -> "KISClient":
    """BrokerAccount 모델 → 복호화 → KISClient."""
    key    = decrypt_secret(account.api_key_enc)
    secret = decrypt_secret(account.api_secret_enc)
    return KISClient(key, secret, account.account_no, account.account_type.value == "REAL")

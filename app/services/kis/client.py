"""
KIS(한국투자증권) OpenAPI 네이티브 클라이언트.
httpx로 REST API 직접 호출. pykis 의존성 없음.

계좌번호 형식: "00000000-01" → CANO="00000000", ACNT_PRDT_CD="01"
토큰: 인스턴스당 1회 발급, 만료 5분 전 자동 갱신
"""
import logging
import threading
import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from dataclasses import dataclass

import json
from pathlib import Path

import httpx


class _RateLimiter:
    """초당 최대 N회 호출을 보장하는 sliding window rate limiter."""

    def __init__(self, max_per_second: int = 18):  # 한도 20, 여유 2 확보
        self._lock = threading.Lock()
        self._timestamps: list[float] = []
        self._max = max_per_second

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            self._timestamps = [t for t in self._timestamps if now - t < 1.0]
            if len(self._timestamps) >= self._max:
                wait = 1.0 - (now - self._timestamps[0])
                if wait > 0:
                    time.sleep(wait)
                self._timestamps = [t for t in self._timestamps if time.monotonic() - t < 1.0]
            self._timestamps.append(time.monotonic())


_rate_limiter = _RateLimiter()

from app.core.config import get_settings
from app.core.security import decrypt_secret

# /tmp는 재부팅 시 초기화되므로 홈 디렉터리에 저장
_TOKEN_CACHE_PATH = Path.home() / ".kis_token_cache.json"
# 토큰 신규 발급 시 동시 발급 방지 (전역 락)
_token_issue_lock = threading.Lock()
# 계좌별 KISClient 싱글턴 레지스트리 (인메모리 토큰 캐시 공유 목적)
_client_registry: dict[str, "KISClient"] = {}
_registry_lock = threading.Lock()

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
    """KIS OpenAPI 클라이언트 (국내/해외 주식)."""

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
        # 1) 인메모리 캐시 확인 (싱글턴 인스턴스면 대부분 여기서 리턴)
        if self._token and self._token_exp and datetime.now() < self._token_exp:
            return

        # 2) 파일 캐시 확인 (프로세스 재시작 후 첫 호출 대응)
        cached = self._load_token_cache()
        if cached:
            self._token     = cached["token"]
            self._token_exp = datetime.fromisoformat(cached["exp"])
            return

        # 3) 신규 발급 — 전역 락으로 동시 발급 방지 (서버 기동 시 여러 잡 동시 시작 대응)
        with _token_issue_lock:
            # 락 획득 후 다시 확인 (대기 중 다른 스레드가 발급 완료했을 수 있음)
            cached = self._load_token_cache()
            if cached:
                self._token     = cached["token"]
                self._token_exp = datetime.fromisoformat(cached["exp"])
                return

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
        _rate_limiter.acquire()
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
        _rate_limiter.acquire()
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

    def get_stock_basic_info(self, stock_code: str) -> dict | None:
        """
        종목 기본정보 조회 (종목명, 시장, 섹터).
        search-stock-info / CTPF1002R
        반환: {"stock_code", "stock_name", "market", "sector"} or None
        """
        try:
            data = self._get(
                "/uapi/domestic-stock/v1/quotations/search-stock-info",
                "CTPF1002R",
                {"PRDT_TYPE_CD": "300", "PDNO": stock_code},
            )
            o = data.get("output", {})
            name = o.get("prdt_abrv_name") or o.get("prdt_name", "")
            if not name:
                return None
            mket = o.get("mket_id_cd", "")
            market = "KOSDAQ" if mket == "KSQ" else "KOSPI"
            sector = o.get("idx_bztp_mcls_cd_name") or o.get("std_idst_clsf_cd_name", "")
            return {"stock_code": stock_code, "stock_name": name, "market": market, "sector": sector}
        except Exception:
            return None

    def get_intraday_status(self, stock_code: str) -> dict:
        """
        09:20 매수 확인용 장중 스냅샷.
        반환: current_price, open_price, today_high, acml_vol, prev_day_vol, transaction_strength
        """
        data = self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            "FHKST01010100",
            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": stock_code},
        )
        o = data.get("output", {})
        bars = self.get_ohlcv(stock_code, days=5)
        prev_day_vol = bars[0].volume if bars else 0

        return {
            "current_price":          int(o.get("stck_prpr") or 0),
            "open_price":             int(o.get("stck_oprc") or 0),
            "today_high":             int(o.get("stck_hgpr") or 0),
            "acml_vol":               int(o.get("acml_vol") or 0),
            "prev_day_vol":           prev_day_vol,
            "transaction_strength":   float(o.get("cttr") or 0),
        }

    def _get_index_level(self, code: str) -> Decimal | None:
        """KOSPI(0001)/KOSDAQ(1001) 현재 지수 레벨 조회."""
        try:
            data = self._get(
                "/uapi/domestic-stock/v1/quotations/inquire-index-price",
                "FHPUP02100000",
                {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": code},
            )
            val = data.get("output", {}).get("bstp_nmix_prpr")
            return Decimal(val) if val else None
        except Exception:
            return None

    def get_index_change_pct(self) -> dict:
        """KOSPI/KOSDAQ 현재 등락률 조회.
        조회 실패 시 해당 지수 값은 None — 0.0(평온한 시장)으로 위장하지 않는다.
        호출자는 None을 '확인 불가'로 취급해 안전 방향으로 처리할 것 (fail-safe).
        """
        result = {}
        for name, code in [("KOSPI", "0001"), ("KOSDAQ", "1001")]:
            try:
                data = self._get(
                    "/uapi/domestic-stock/v1/quotations/inquire-index-price",
                    "FHPUP02100000",
                    {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": code},
                )
                o = data.get("output", {})
                val = o.get("bstp_nmix_prdy_ctrt")
                result[name] = float(val) if val not in (None, "") else None
            except Exception as e:
                logger.warning("Index change fetch failed (%s): %s", name, e)
                result[name] = None
        return result

    def get_index_overview(self, code: str) -> dict:
        """단일 지수 레벨 + 등락률을 1회 API 호출로 반환. code: '0001'=KOSPI, '1001'=KOSDAQ"""
        try:
            data = self._get(
                "/uapi/domestic-stock/v1/quotations/inquire-index-price",
                "FHPUP02100000",
                {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": code},
            )
            o = data.get("output", {})
            return {
                "level":      float(o.get("bstp_nmix_prpr") or 0),
                "change_pct": float(o.get("bstp_nmix_prdy_ctrt") or 0),
            }
        except Exception:
            return {"level": 0.0, "change_pct": 0.0}

    def get_index_daily_closes(self, code: str = "0001", days: int = 6) -> list[float]:
        """
        지수 일봉 종가 조회 (최신 → 오래된 순, 당일 미완성 봉 제외).
        FHKUP03500100 inquire-daily-indexchartprice. code: '0001'=KOSPI, '1001'=KOSDAQ
        A-gate 수치 판정용 — 장 시작 전(08:30)에도 전일까지의 확정 종가만 반환.
        """
        today = date.today().strftime("%Y%m%d")
        closes: list[float] = []
        seen: set[str] = set()
        end = date.today()
        # 호출당 약 50행 제한 — days가 크면 날짜 구간을 나눠 연속 조회
        while len(closes) < days:
            start = (end - timedelta(days=75)).strftime("%Y%m%d")  # ≈ 50거래일 여유
            data = self._get(
                "/uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice",
                "FHKUP03500100",
                {
                    "FID_COND_MRKT_DIV_CODE": "U",
                    "FID_INPUT_ISCD":         code,
                    "FID_INPUT_DATE_1":       start,
                    "FID_INPUT_DATE_2":       end.strftime("%Y%m%d"),
                    "FID_PERIOD_DIV_CODE":    "D",
                },
            )
            oldest: str | None = None
            got_new = False
            for item in data.get("output2", []):
                bar_date = item.get("stck_bsop_date", "")
                close = item.get("bstp_nmix_prpr")
                if not close or not bar_date or bar_date >= today or bar_date in seen:
                    continue  # 당일 봉은 미확정 — 제외
                seen.add(bar_date)
                closes.append(float(close))
                got_new = True
                oldest = bar_date if oldest is None or bar_date < oldest else oldest
                if len(closes) >= days:
                    break
            if not got_new or not oldest:
                break  # 데이터 소진
            end = datetime.strptime(oldest, "%Y%m%d").date() - timedelta(days=1)
        return closes[:days]

    def get_current_price(self, stock_code: str) -> Decimal:
        """현재가 조회."""
        data = self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            "FHKST01010100",
            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": stock_code},
        )
        return Decimal(data["output"]["stck_prpr"])

    def get_price_with_change(self, stock_code: str) -> dict:
        """현재가 + 전일대비 등락률을 1회 API 호출로 반환."""
        data = self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            "FHKST01010100",
            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": stock_code},
        )
        o = data.get("output", {})
        price = int(o.get("stck_prpr") or 0)
        change_pct = float(o.get("prdy_ctrt") or 0)
        # prdy_ctrt가 가격값을 반환하는 경우 방어: prdy_vrss/sign으로 직접 계산
        if abs(change_pct) > 100:
            vrss = float(o.get("prdy_vrss") or 0)
            sign_code = o.get("prdy_vrss_sign", "3")
            sign = 1 if sign_code in ("1", "2") else (-1 if sign_code in ("4", "5") else 0)
            prev_close = price - vrss * sign
            change_pct = round(vrss * sign / prev_close * 100, 2) if prev_close else 0.0
        return {"price": price, "change_pct": change_pct}

    def get_quote(self, stock_code: str) -> dict:
        """호가 조회 (FHKST01010200) — 매도호가1/매수호가1. 값 없으면(장외 등) None."""
        data = self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn",
            "FHKST01010200",
            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": stock_code},
        )
        o = data.get("output1", {})
        ask1 = Decimal(o.get("askp1") or "0")
        bid1 = Decimal(o.get("bidp1") or "0")
        return {
            "ask1": ask1 if ask1 > 0 else None,
            "bid1": bid1 if bid1 > 0 else None,
        }

    def get_us_price_with_change(self, symbol: str, exchange: str) -> dict:
        """해외주식 현재가 + 등락률 1회 API 호출로 반환. 반환: {price: float, change_pct: float}"""
        data = self._get(
            "/uapi/overseas-price/v1/quotations/price",
            "HHDFS00000300",
            {"AUTH": "", "EXCD": exchange.upper(), "SYMB": symbol.upper()},
        )
        o = data.get("output", {})
        return {
            "price":      float(o.get("last") or 0),
            "change_pct": float(str(o.get("rate") or "0").replace("+", "")),
        }

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
    # 관심종목 분석용 (docs/watchlist_spec.md)
    # 실패 시 None/[] 반환 — 가짜 값(0 등)으로 위장 금지, 호출부가 "데이터 없음" 플래그 처리
    # ------------------------------------------------------------------ #

    @staticmethod
    def _float(v) -> float | None:
        try:
            f = float(v)
        except (ValueError, TypeError):
            return None
        return f

    def get_financial_ratios(self, stock_code: str, quarterly: bool = True) -> list[dict]:
        """
        재무비율 조회 (FHKST66430300). 분기별 ROE/부채비율/EPS/BPS/성장률.
        반환: 최신순 [{period, sales_growth, op_profit_growth, net_income_growth,
                       roe, eps, bps, debt_ratio, reserve_rate}], 실패 시 []
        """
        try:
            data = self._get(
                "/uapi/domestic-stock/v1/finance/financial-ratio",
                "FHKST66430300",
                {"FID_DIV_CLS_CODE": "1" if quarterly else "0",
                 "fid_cond_mrkt_div_code": "J", "fid_input_iscd": stock_code},
            )
        except Exception as e:
            logger.warning("financial_ratios failed for %s: %s", stock_code, e)
            return []
        return [
            {
                "period":            r.get("stac_yymm"),
                "sales_growth":      self._float(r.get("grs")),
                "op_profit_growth":  self._float(r.get("bsop_prfi_inrt")),
                "net_income_growth": self._float(r.get("ntin_inrt")),
                "roe":               self._float(r.get("roe_val")),
                "eps":               self._float(r.get("eps")),
                "bps":               self._float(r.get("bps")),
                "debt_ratio":        self._float(r.get("lblt_rate")),
                "reserve_rate":      self._float(r.get("rsrv_rate")),
            }
            for r in data.get("output", [])
        ]

    def get_income_statements(self, stock_code: str, quarterly: bool = True) -> list[dict]:
        """
        손익계산서 조회 (FHKST66430200). 단위: 억원.
        주의: 분기 조회 시 각 행은 해당 분기말까지의 **YTD 누적**값 —
        단일 분기 값은 호출부에서 차분으로 계산해야 함.
        반환: 최신순 [{period, revenue, operating_profit, net_income}], 실패 시 []
        """
        try:
            data = self._get(
                "/uapi/domestic-stock/v1/finance/income-statement",
                "FHKST66430200",
                {"FID_DIV_CLS_CODE": "1" if quarterly else "0",
                 "fid_cond_mrkt_div_code": "J", "fid_input_iscd": stock_code},
            )
        except Exception as e:
            logger.warning("income_statements failed for %s: %s", stock_code, e)
            return []
        return [
            {
                "period":           r.get("stac_yymm"),
                "revenue":          self._float(r.get("sale_account")),
                "operating_profit": self._float(r.get("bsop_prti")),
                "net_income":       self._float(r.get("thtr_ntin")),
            }
            for r in data.get("output", [])
        ]

    # estimate-perform output2/3는 필드명이 data1~5(연도)이고 행 순서가 항목을 의미.
    # 행 라벨은 2026-07-02 삼성전자 실응답을 재무비율/손익계산서 실적치와 교차검증해 확정
    # (영업이익 436011억·증감율 33.2%·ROE 10.85·부채비율 29.94 일치 확인).
    # 비율·EPS 계열은 ×10 스케일로 옴 → /10, 금액 계열은 억원 그대로.
    _ESTIMATE_O2_ROWS = [
        ("revenue", 1), ("revenue_growth", 10),
        ("operating_profit", 1), ("op_growth", 10),
        ("net_income", 1), ("ni_growth", 10),
    ]
    _ESTIMATE_O3_ROWS = [
        ("ebitda", 1), ("eps", 10), ("eps_growth", 10), ("per", 10),
        ("ev_ebitda", 10), ("roe", 10), ("debt_ratio", 10), ("interest_coverage", 10),
    ]

    def get_estimate_perform(self, stock_code: str) -> dict | None:
        """
        종목추정실적 조회 (HHKST668300C0) — 증권사 컨센서스 기반 연도별 추정.
        반환: {analyst, opinion, est_date, periods, revenue[], operating_profit[],
               eps[], per[], roe[], ...} (periods와 index 정렬, 'E' 접미사=추정치)
        커버리지 없는 종목/실패 시 None. ※ 목표주가는 이 API에 없음.
        """
        try:
            data = self._get(
                "/uapi/domestic-stock/v1/quotations/estimate-perform",
                "HHKST668300C0",
                {"SHT_CD": stock_code},
            )
        except Exception as e:
            logger.warning("estimate_perform failed for %s: %s", stock_code, e)
            return None

        periods = [r.get("dt") for r in data.get("output4", []) if r.get("dt")]
        o1 = data.get("output1") or {}
        if not periods or not o1.get("item_kor_nm"):
            return None  # 컨센서스 커버리지 없음

        def _rows(rows: list, labels: list[tuple[str, int]]) -> dict:
            out = {}
            for (label, scale), row in zip(labels, rows):
                vals = []
                for i in range(1, len(periods) + 1):
                    v = self._float(row.get(f"data{i}"))
                    vals.append(round(v / scale, 2) if v is not None else None)
                out[label] = vals
            return out

        result = {
            "analyst":  o1.get("name1") or None,
            "opinion":  o1.get("rcmd_name") or None,
            "est_date": o1.get("estdate") or None,
            "periods":  periods,
        }
        result.update(_rows(data.get("output2", []), self._ESTIMATE_O2_ROWS))
        result.update(_rows(data.get("output3", []), self._ESTIMATE_O3_ROWS))
        return result

    def get_investor_daily(self, stock_code: str) -> list[dict]:
        """
        일별 투자자 순매수 (FHKST01010900) — API 한계로 최근 30거래일까지만.
        amt 단위: 백만원. 반환: 최신순, 실패 시 []
        """
        try:
            data = self._get(
                "/uapi/domestic-stock/v1/quotations/inquire-investor",
                "FHKST01010900",
                {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": stock_code},
            )
        except Exception as e:
            logger.warning("investor_daily failed for %s: %s", stock_code, e)
            return []
        return [
            {
                "date":          r.get("stck_bsop_date"),
                "close":         self._float(r.get("stck_clpr")),
                "frgn_ntby_qty": self._float(r.get("frgn_ntby_qty")),
                "orgn_ntby_qty": self._float(r.get("orgn_ntby_qty")),
                "prsn_ntby_qty": self._float(r.get("prsn_ntby_qty")),
                "frgn_ntby_amt": self._float(r.get("frgn_ntby_tr_pbmn")),
                "orgn_ntby_amt": self._float(r.get("orgn_ntby_tr_pbmn")),
                "prsn_ntby_amt": self._float(r.get("prsn_ntby_tr_pbmn")),
            }
            for r in data.get("output", [])
        ]

    def get_foreign_holding(self, stock_code: str) -> dict | None:
        """외국인 보유 현황 (inquire-price 동봉 필드). 현재 시점 값만 제공됨."""
        try:
            data = self._get(
                "/uapi/domestic-stock/v1/quotations/inquire-price",
                "FHKST01010100",
                {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": stock_code},
            )
            o = data.get("output", {})
            return {
                "frgn_exhaust_rate": self._float(o.get("hts_frgn_ehrt")),   # 외국인 소진율(%)
                "frgn_holding_qty":  self._float(o.get("frgn_hldn_qty")),
                "per": self._float(o.get("per")), "pbr": self._float(o.get("pbr")),
                "eps": self._float(o.get("eps")), "bps": self._float(o.get("bps")),
                "current_price": self._float(o.get("stck_prpr")),
                "change_pct":    self._float(o.get("prdy_ctrt")),
                "market_cap_eok": self._float(o.get("hts_avls")),  # 시가총액(억원) — TTM PER 계산용
            }
        except Exception as e:
            logger.warning("foreign_holding failed for %s: %s", stock_code, e)
            return None

    def get_ohlcv_long(self, stock_code: str, days: int = 180) -> list[OHLCVBar]:
        """
        일봉 장기 조회 — inquire-daily-itemchartprice가 호출당 100건 제한이라
        날짜 구간을 나눠 연속 조회. 반환: 최신순 최대 days개.
        """
        bars: list[OHLCVBar] = []
        end = date.today()
        seen: set[str] = set()
        while len(bars) < days:
            start = end - timedelta(days=170)  # 100거래일 ≈ 145달력일, 여유 포함
            data = self._get(
                "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
                "FHKST03010100",
                {
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_INPUT_ISCD":         stock_code,
                    "FID_INPUT_DATE_1":       start.strftime("%Y%m%d"),
                    "FID_INPUT_DATE_2":       end.strftime("%Y%m%d"),
                    "FID_PERIOD_DIV_CODE":    "D",
                    "FID_ORG_ADJ_PRC":        "0",
                },
            )
            chunk = []
            for item in data.get("output2", []):
                close = item.get("stck_clpr", "0")
                d = item.get("stck_bsop_date")
                if not close or close == "0" or not d or d in seen:
                    continue
                seen.add(d)
                chunk.append(OHLCVBar(
                    date=d,
                    open=Decimal(item.get("stck_oprc") or close),
                    high=Decimal(item.get("stck_hgpr") or close),
                    low=Decimal(item.get("stck_lwpr") or close),
                    close=Decimal(close),
                    volume=int(item.get("acml_vol") or 0),
                ))
            if not chunk:
                break  # 상장 이전 구간 도달
            bars.extend(chunk)
            end = datetime.strptime(chunk[-1].date, "%Y%m%d").date() - timedelta(days=1)
        return bars[:days]

    def get_ohlcv_monthly(self, stock_code: str, months: int = 60) -> list[OHLCVBar]:
        """
        월봉 조회 (최신 → 오래된 순). FID_PERIOD_DIV_CODE='M' — 호출 1회로 최대 100개월.
        PBR 과거 밴드 근사 계산용 (월별 종가 ÷ 해당 연도 BPS).
        """
        today = date.today()
        start = date(today.year - (months // 12 + 1), today.month, 1)
        data = self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            "FHKST03010100",
            {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD":         stock_code,
                "FID_INPUT_DATE_1":       start.strftime("%Y%m%d"),
                "FID_INPUT_DATE_2":       today.strftime("%Y%m%d"),
                "FID_PERIOD_DIV_CODE":    "M",
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
        return bars[:months]

    def get_fx_daily_closes(self, symbol: str = "FX@KRW", days: int = 65) -> list[dict]:
        """
        환율 일별 종가 (최신 → 오래된 순). FHKST03030100 해외 종목/지수/환율 기간별시세,
        FID_COND_MRKT_DIV_CODE='X'(환율). 기본 symbol 'FX@KRW' = USD/KRW.
        반환: [{date: 'YYYYMMDD', close: float}], 실패 시 []
        """
        today = date.today()
        start = today - timedelta(days=int(days * 1.6) + 15)
        try:
            data = self._get(
                "/uapi/overseas-price/v1/quotations/inquire-daily-chartprice",
                "FHKST03030100",
                {
                    "FID_COND_MRKT_DIV_CODE": "X",
                    "FID_INPUT_ISCD":         symbol,
                    "FID_INPUT_DATE_1":       start.strftime("%Y%m%d"),
                    "FID_INPUT_DATE_2":       today.strftime("%Y%m%d"),
                    "FID_PERIOD_DIV_CODE":    "D",
                },
            )
        except Exception as e:
            logger.warning("fx_daily failed for %s: %s", symbol, e)
            return []
        rows = []
        for item in data.get("output2", []):
            d = item.get("stck_bsop_date")
            close = self._float(item.get("ovrs_nmix_prpr"))
            if d and close:
                rows.append({"date": d, "close": close})
        rows.sort(key=lambda r: r["date"], reverse=True)
        return rows[:days]

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
    def _compute_atr(bars: list[OHLCVBar], period: int = 14) -> float | None:
        """Average True Range. bars는 최신→오래된 순 (get_ohlcv 기본 순서)."""
        if len(bars) < period + 1:
            return None
        # 오래된→최신 순으로 변환
        sorted_bars = list(reversed(bars))
        true_ranges = []
        for i in range(1, len(sorted_bars)):
            high       = float(sorted_bars[i].high)
            low        = float(sorted_bars[i].low)
            prev_close = float(sorted_bars[i - 1].close)
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            true_ranges.append(tr)
        if len(true_ranges) < period:
            return None
        return sum(true_ranges[-period:]) / period

    @staticmethod
    def _compute_mas(bars: list[OHLCVBar]) -> dict[str, Decimal | None]:
        closes = [float(b.close) for b in reversed(bars)]

        def ma(n: int) -> Decimal | None:
            if len(closes) < n:
                return None
            return Decimal(str(round(sum(closes[-n:]) / n, 0)))

        return {"ma5": ma(5), "ma20": ma(20), "ma60": ma(min(60, len(closes)))}

    # ------------------------------------------------------------------ #
    # 해외주식 시세 조회
    # ------------------------------------------------------------------ #

    def get_us_current_price(self, symbol: str, exchange: str) -> Decimal:
        """해외주식 현재가 조회."""
        data = self._get(
            "/uapi/overseas-price/v1/quotations/price",
            "HHDFS00000300",
            {"AUTH": "", "EXCD": exchange.upper(), "SYMB": symbol.upper()},
        )
        return Decimal(data["output"]["last"])

    def get_us_ohlcv(self, symbol: str, exchange: str, days: int = 100) -> list[OHLCVBar]:
        """해외주식 일봉 OHLCV 조회."""
        today = date.today().strftime("%Y%m%d")
        data = self._get(
            "/uapi/overseas-price/v1/quotations/dailyprice",
            "HHDFS76240000",
            {
                "AUTH": "",
                "EXCD": exchange.upper(),
                "SYMB": symbol.upper(),
                "GUBN": "0",
                "BYMD": today,
                "MODP": "0",
            },
        )
        bars = []
        for item in data.get("output2", []):
            close = item.get("clos", "0")
            if not close or close == "0":
                continue
            bars.append(OHLCVBar(
                date=item["xymd"],
                open=Decimal(item.get("open") or close),
                high=Decimal(item.get("high") or close),
                low=Decimal(item.get("low") or close),
                close=Decimal(close),
                volume=int(item.get("tvol") or 0),
            ))
        return bars[:days]

    def _get_us_stock_info(self, symbol: str, exchange: str) -> dict:
        """해외주식 통합 정보 (현재가 + OHLCV + 기술적지표). currency=USD."""
        current_price = self.get_us_current_price(symbol, exchange)
        bars          = self.get_us_ohlcv(symbol, exchange)
        rsi           = self._compute_rsi(bars)
        mas           = self._compute_mas(bars)

        recent     = bars[:5] if bars else []
        avg_volume = int(sum(b.volume for b in bars[:20]) / min(20, len(bars))) if bars else 0

        def _price(v) -> float | None:
            return float(v) if v else None

        return {
            "stock_code":      symbol,
            "currency":        "USD",
            "current_price":   float(current_price),
            "rsi_14":          float(rsi) if rsi else None,
            "ma5":             _price(mas["ma5"]),
            "ma20":            _price(mas["ma20"]),
            "ma60":            _price(mas["ma60"]),
            "avg_volume_20d":  avg_volume,
            "frgn_net_buy_1d": 0,
            "frgn_net_buy_5d": 0,
            "orgn_net_buy_1d": 0,
            "orgn_net_buy_5d": 0,
            "recent_ohlcv": [
                {
                    "date":   b.date,
                    "open":   float(b.open),
                    "high":   float(b.high),
                    "low":    float(b.low),
                    "close":  float(b.close),
                    "volume": b.volume,
                }
                for b in recent
            ],
        }

    def _get_domestic_stock_info(self, stock_code: str) -> dict:
        """국내주식 통합 정보 (현재가 + OHLCV + 기술적지표 + 외국인/기관 + 밸류). currency=KRW."""
        price_data    = self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            "FHKST01010100",
            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": stock_code},
        )
        po            = price_data.get("output", {})
        current_price = Decimal(po.get("stck_prpr") or "0")
        # 밸류 참고치 (적자기업은 per≤0 → None 처리, 0으로 위장 금지). 추가 API 호출 없음.
        per           = float(po.get("per") or 0) or None
        eps           = int(float(po.get("eps") or 0)) or None   # KIS는 "6564.00" 문자열 반환
        if per is not None and per <= 0:
            per = None
        bars          = self.get_ohlcv(stock_code)
        rsi           = self._compute_rsi(bars)
        mas           = self._compute_mas(bars)
        investor      = self.get_investor_trend(stock_code)

        recent     = bars[:5] if bars else []
        avg_volume = int(sum(b.volume for b in bars[:20]) / min(20, len(bars))) if bars else 0

        return {
            "stock_code":      stock_code,
            "currency":        "KRW",
            "current_price":   int(current_price),
            "rsi_14":          float(rsi) if rsi else None,
            "ma5":             int(mas["ma5"])  if mas["ma5"]  else None,
            "ma20":            int(mas["ma20"]) if mas["ma20"] else None,
            "ma60":            int(mas["ma60"]) if mas["ma60"] else None,
            "avg_volume_20d":  avg_volume,
            "per":             per,    # 주가수익비율 (참고용, 적자기업=None)
            "eps":             eps,    # 주당순이익 (참고용)
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

    def get_historical_stock_info(self, stock_code: str, target_date) -> dict | None:
        """target_date 기준 과거 시점 주식 데이터를 OHLCV에서 재구성. 백테스트용."""
        try:
            bars = self.get_ohlcv(stock_code, days=100)
            if not bars:
                return None

            # OHLCV date 형식은 "YYYYMMDD", target_date를 같은 형식으로 변환
            from datetime import date as date_type
            if isinstance(target_date, date_type):
                target_str = target_date.strftime("%Y%m%d")
            else:
                target_str = str(target_date).replace("-", "")

            # bars는 최신순 정렬 → 오래된 순으로 뒤집기
            sorted_bars = sorted(bars, key=lambda b: b.date)
            hist_bars = [b for b in sorted_bars if b.date <= target_str]
            if not hist_bars:
                return None

            target_bar = hist_bars[-1]   # target_date에 가장 가까운 영업일
            rsi = self._compute_rsi(hist_bars)
            mas = self._compute_mas(hist_bars)
            avg_volume = int(sum(b.volume for b in hist_bars[-20:]) / min(20, len(hist_bars)))
            recent = hist_bars[-5:]      # 가장 최근 5개

            return {
                "stock_code":      stock_code,
                "currency":        "KRW",
                "current_price":   int(target_bar.close),
                "rsi_14":          float(rsi) if rsi else None,
                "ma5":             int(mas["ma5"])  if mas["ma5"]  else None,
                "ma20":            int(mas["ma20"]) if mas["ma20"] else None,
                "ma60":            int(mas["ma60"]) if mas["ma60"] else None,
                "avg_volume_20d":  avg_volume,
                "per":             None,   # 백테스트는 과거 밸류 재현 불가
                "eps":             None,
                "frgn_net_buy_1d": 0,
                "frgn_net_buy_5d": 0,
                "orgn_net_buy_1d": 0,
                "orgn_net_buy_5d": 0,
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
        except Exception as e:
            logger.warning("Historical stock info failed for %s@%s: %s", stock_code, target_date, e)
            return None

    # ------------------------------------------------------------------ #
    # 통합 종목 정보 (Gemini Stage4 입력용)
    # ------------------------------------------------------------------ #

    def get_stock_info(self, stock_code: str, country: str = "KR", market: str | None = None) -> dict:
        """현재가 + OHLCV + 기술적지표 통합 조회. country='US'이면 해외주식 경로 사용."""
        if country.upper() == "US":
            exchange = (market or "NAS").upper()
            return self._get_us_stock_info(stock_code, exchange)
        return self._get_domestic_stock_info(stock_code)

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

    def get_today_fill_price(self, stock_code: str, side: str = "02") -> Decimal | None:
        """
        당일 특정 종목 체결가 조회 (TTTC8001R).
        side="02" → 매수 체결가 (entry_price용)
        side="01" → 매도 체결가 (exit_price용)
        INQR_DVSN="00" (역순) 이므로 output1 첫 항목이 가장 최근 주문.
        """
        today = date.today().strftime("%Y%m%d")
        tr_id = "TTTC8001R" if self._is_real else "VTTC8001R"
        try:
            data = self._get(
                "/uapi/domestic-stock/v1/trading/inquire-daily-ccld",
                tr_id,
                {
                    "CANO":            self._cano,
                    "ACNT_PRDT_CD":    self._acnt_prdt,
                    "INQR_STRT_DT":    today,
                    "INQR_END_DT":     today,
                    "SLL_BUY_DVSN_CD": side,
                    "INQR_DVSN":       "00",    # 역순 → 첫 항목 = 최신 주문
                    "PDNO":            stock_code,
                    "CCLD_DVSN":       "01",    # 체결분만
                    "ORD_GNO_BRNO":    "",
                    "ODNO":            "",
                    "INQR_DVSN_3":     "00",
                    "INQR_DVSN_1":     "",
                    "CTX_AREA_FK100":  "",
                    "CTX_AREA_NK100":  "",
                },
            )
            for item in data.get("output1", []):
                qty = int(item.get("tot_ccld_qty") or 0)
                if qty <= 0:
                    continue
                avg = item.get("avg_prvs") or ""
                if avg and Decimal(avg) > 0:
                    return Decimal(avg)
                amt = Decimal(item.get("tot_ccld_amt") or "0")
                if amt > 0:
                    return (amt / qty).quantize(Decimal("1"))
        except Exception as e:
            logger.warning("get_today_fill_price failed for %s side=%s: %s", stock_code, side, e)
        return None

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
    from app.models.user import BrokerAccount, AccountType

    # VIRTUAL 계좌는 KIS 키가 없어 시장 데이터 클라이언트로 쓸 수 없다 — REAL 우선
    account = db.scalar(
        select(BrokerAccount)
        .where(
            BrokerAccount.is_active == True,  # noqa: E712
            BrokerAccount.account_type != AccountType.VIRTUAL,
        )
        .order_by(BrokerAccount.account_type)  # enum 정의 순: REAL → PAPER
        .limit(1)
    )
    if not account:
        raise RuntimeError("활성 broker_account가 없습니다. DB에 계좌를 먼저 등록하세요.")
    return get_kis_client_from_account(account)


def get_kis_client_from_account(account) -> "KISClient":
    """BrokerAccount 모델 → 복호화 → KISClient 싱글턴 반환.
    같은 account_id에 대해 항상 동일 인스턴스를 반환해 인메모리 토큰 캐시를 공유한다."""
    if account.account_type.value == "VIRTUAL":
        # 가상계좌는 KIS 키가 없다 — get_trading_client(virtual_broker)를 써야 한다
        raise RuntimeError(f"VIRTUAL 계좌({account.account_id})로 KISClient를 만들 수 없습니다")
    account_id = str(account.account_id)
    with _registry_lock:
        if account_id not in _client_registry:
            key    = decrypt_secret(account.api_key_enc)
            secret = decrypt_secret(account.api_secret_enc)
            _client_registry[account_id] = KISClient(
                key, secret, account.account_no,
                account.account_type.value == "REAL",
            )
        return _client_registry[account_id]

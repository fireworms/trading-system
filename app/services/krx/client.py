"""
KRX 오픈API 어댑터 (일별 전종목 시세 벌크 수집).

KIS는 종목당 1회 호출이라 전종목 히스토리를 긁으려면 수천 번 호출해야 하는데,
KRX는 **하루치 전종목을 1회 호출로** 준다 (KOSPI 943 + KOSDAQ 1822 = 2765행/일).
백테스트 파라미터 튜닝처럼 넓은 과거 구간이 필요한 작업에 쓴다.

2026-09-03 실검증 사항 (문서/외부 사용례와 달랐던 것들):
- **호스트는 `data-dbg.krx.co.kr`**. `openapi.krx.co.kr`은 포털(로그인 UI) 전용이라
  API 경로가 전부 404다. 이걸로 반나절 날릴 수 있음
- 응답 래퍼는 항상 `OutBlock_1` 배열
- **`ISU_CD`의 의미가 엔드포인트마다 다르다**: 일별매매는 6자리 단축코드,
  종목기본정보는 12자리 ISIN(단축코드는 `ISU_SRT_CD`). 조인할 때 주의
- 주말·휴장일·미래 날짜는 에러가 아니라 `200 + 빈 배열` → 스킵 처리
- **401 = 해당 서비스 미이용신청** (404 = 경로 오류). 서비스별로 개별 신청 필요
- `idx/krx_dd_trd`는 KRX 시리즈 40종만 — **코스피 지수는 없다**(`idx/kospi_dd_trd` 별도)

공통 원칙: 키 미설정·API 실패로 호출부가 죽지 않는다. 예외 대신
{"available": False, "note": ...} 폴백을 반환한다 (DART 어댑터와 동일).
"""
import logging
from datetime import date

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_BASE_URL = "https://data-dbg.krx.co.kr/svc/apis"
_TIMEOUT = 60.0

# 카테고리/엔드포인트 ID — 2026-09-03 실호출로 확인
ENDPOINTS = {
    "kospi_daily":    "/sto/stk_bydd_trd",       # 유가증권 일별매매정보
    "kosdaq_daily":   "/sto/ksq_bydd_trd",       # 코스닥 일별매매정보
    "kospi_issues":   "/sto/stk_isu_base_info",  # 유가증권 종목기본정보
    "kosdaq_issues":  "/sto/ksq_isu_base_info",  # 코스닥 종목기본정보
    "krx_index":      "/idx/krx_dd_trd",         # KRX 시리즈 지수 (40종, 코스피 미포함)
    "kospi_index":    "/idx/kospi_dd_trd",       # 코스피 지수 시리즈
    "kosdaq_index":   "/idx/kosdaq_dd_trd",      # 코스닥 지수 시리즈 (2026-09-03 기준 미신청=401)
}

# 일별매매정보 응답 필드 → 우리 컬럼. 스키마가 바뀌면 여기만 고친다.
_DAILY_FIELD_MAP = {
    "stock_code":    "ISU_CD",         # 일별매매에서는 6자리 단축코드
    "stock_name":    "ISU_NM",
    "market":        "MKT_NM",
    "sector_type":   "SECT_TP_NM",
    "close":         "TDD_CLSPRC",
    "change":        "CMPPREVDD_PRC",
    "change_pct":    "FLUC_RT",
    "open":          "TDD_OPNPRC",
    "high":          "TDD_HGPRC",
    "low":           "TDD_LWPRC",
    "volume":        "ACC_TRDVOL",
    "trade_value":   "ACC_TRDVAL",
    "market_cap":    "MKTCAP",
    "listed_shares": "LIST_SHRS",
}


class KRXAuthError(RuntimeError):
    """해당 서비스 미이용신청(401). 키 자체는 유효할 수 있다."""


def _to_num(v):
    """KRX는 모든 값을 문자열로 준다. 빈 문자열·'-'는 None."""
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    if s in ("", "-", "N/A"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _fetch(endpoint_key: str, bas_dd: str) -> list[dict]:
    """단일 엔드포인트 1일치 조회 → OutBlock_1 배열.

    빈 배열은 정상(휴장일). 401은 KRXAuthError로 구분해 올린다.
    """
    settings = get_settings()
    if not settings.krx_api_key:
        raise RuntimeError("KRX_API_KEY 미설정")

    path = ENDPOINTS[endpoint_key]
    resp = httpx.get(
        f"{_BASE_URL}{path}",
        params={"basDd": bas_dd},
        headers={"AUTH_KEY": settings.krx_api_key},
        timeout=_TIMEOUT,
    )
    if resp.status_code == 401:
        raise KRXAuthError(
            f"{path} 미이용신청 (401). KRX 포털 > 서비스이용에서 해당 API를 신청해야 한다."
        )
    resp.raise_for_status()
    if "json" not in resp.headers.get("content-type", "").lower():
        raise RuntimeError(f"{path} JSON 아닌 응답 (경로 오류 의심): {resp.text[:120]}")
    return resp.json().get("OutBlock_1") or []


def get_daily_trades(bas_dd: date | str, markets=("kospi", "kosdaq")) -> dict:
    """지정일 전종목 일별매매정보.

    반환: {"available": bool, "rows": [정규화된 dict], "note": str}
    휴장일이면 available=True + rows=[] (실패가 아니라 확정 사실).
    """
    day = bas_dd.strftime("%Y%m%d") if isinstance(bas_dd, date) else str(bas_dd)
    rows: list[dict] = []
    notes: list[str] = []

    for mkt in markets:
        try:
            raw = _fetch(f"{mkt}_daily", day)
        except KRXAuthError as e:
            notes.append(str(e))
            continue
        except Exception as e:
            logger.warning("KRX %s %s 조회 실패: %s", mkt, day, e)
            notes.append(f"{mkt}: {e}")
            continue

        for r in raw:
            code = (r.get(_DAILY_FIELD_MAP["stock_code"]) or "").strip()
            if not code:
                continue
            rows.append({
                "trade_date":    day,
                "stock_code":    code,
                "stock_name":    (r.get(_DAILY_FIELD_MAP["stock_name"]) or "").strip(),
                "market":        (r.get(_DAILY_FIELD_MAP["market"]) or "").strip(),
                "sector_type":   (r.get(_DAILY_FIELD_MAP["sector_type"]) or "").strip() or None,
                "open":          _to_num(r.get(_DAILY_FIELD_MAP["open"])),
                "high":          _to_num(r.get(_DAILY_FIELD_MAP["high"])),
                "low":           _to_num(r.get(_DAILY_FIELD_MAP["low"])),
                "close":         _to_num(r.get(_DAILY_FIELD_MAP["close"])),
                "change":        _to_num(r.get(_DAILY_FIELD_MAP["change"])),
                "change_pct":    _to_num(r.get(_DAILY_FIELD_MAP["change_pct"])),
                "volume":        _to_num(r.get(_DAILY_FIELD_MAP["volume"])),
                "trade_value":   _to_num(r.get(_DAILY_FIELD_MAP["trade_value"])),
                "market_cap":    _to_num(r.get(_DAILY_FIELD_MAP["market_cap"])),
                "listed_shares": _to_num(r.get(_DAILY_FIELD_MAP["listed_shares"])),
            })

    if not rows and notes:
        return {"available": False, "rows": [], "note": "; ".join(notes)}
    return {"available": True, "rows": rows, "note": "; ".join(notes)}


def get_index_daily(bas_dd: date | str, series: str = "kospi_index") -> dict:
    """지정일 지수 일별시세.

    series: kospi_index(코스피 계열) / krx_index(KRX 시리즈 40종) / kosdaq_index(미신청 시 401)
    주의: krx_index에는 코스피 지수가 없다.
    """
    day = bas_dd.strftime("%Y%m%d") if isinstance(bas_dd, date) else str(bas_dd)
    try:
        raw = _fetch(series, day)
    except Exception as e:
        return {"available": False, "rows": [], "note": str(e)}

    return {
        "available": True,
        "rows": [{
            "trade_date": day,
            "index_class": (r.get("IDX_CLSS") or "").strip(),
            "index_name":  (r.get("IDX_NM") or "").strip(),
            "close":       _to_num(r.get("CLSPRC_IDX")),
            "change":      _to_num(r.get("CMPPREVDD_IDX")),
            "change_pct":  _to_num(r.get("FLUC_RT")),
            "open":        _to_num(r.get("OPNPRC_IDX")),
            "high":        _to_num(r.get("HGPRC_IDX")),
            "low":         _to_num(r.get("LWPRC_IDX")),
            "volume":      _to_num(r.get("ACC_TRDVOL")),
            "trade_value": _to_num(r.get("ACC_TRDVAL")),
            "market_cap":  _to_num(r.get("MKTCAP")),
        } for r in raw],
        "note": "",
    }


def get_issue_base_info(bas_dd: date | str, markets=("kospi", "kosdaq")) -> dict:
    """종목기본정보 (상장일·액면가·상장주식수 등). stock_master 갱신용, 일 1회면 충분.

    주의: 여기서 ISU_CD는 12자리 ISIN이고 6자리 단축코드는 ISU_SRT_CD다.
    """
    day = bas_dd.strftime("%Y%m%d") if isinstance(bas_dd, date) else str(bas_dd)
    rows, notes = [], []
    for mkt in markets:
        try:
            raw = _fetch(f"{mkt}_issues", day)
        except Exception as e:
            notes.append(f"{mkt}: {e}")
            continue
        for r in raw:
            code = (r.get("ISU_SRT_CD") or "").strip()   # ISU_CD 아님 (ISIN)
            if not code:
                continue
            rows.append({
                "stock_code":    code,
                "isin":          (r.get("ISU_CD") or "").strip(),
                "stock_name":    (r.get("ISU_ABBRV") or r.get("ISU_NM") or "").strip(),
                "full_name":     (r.get("ISU_NM") or "").strip(),
                "english_name":  (r.get("ISU_ENG_NM") or "").strip(),
                "market":        (r.get("MKT_TP_NM") or "").strip(),
                "sector_type":   (r.get("SECT_TP_NM") or "").strip() or None,
                "security_type": (r.get("KIND_STKCERT_TP_NM") or "").strip(),
                "listed_date":   (r.get("LIST_DD") or "").strip() or None,
                "par_value":     _to_num(r.get("PARVAL")),
                "listed_shares": _to_num(r.get("LIST_SHRS")),
            })
    if not rows and notes:
        return {"available": False, "rows": [], "note": "; ".join(notes)}
    return {"available": True, "rows": rows, "note": "; ".join(notes)}


def check_access() -> dict:
    """키 유효성 + 엔드포인트별 이용신청 상태 점검. 진단용.

    반환: {endpoint_key: "ok(n행)" | "미신청(401)" | "오류: ..."}
    """
    if not get_settings().krx_api_key:
        return {"_key": "미설정"}

    probe_day = "20260902"   # 확정 거래일
    result = {}
    for key in ENDPOINTS:
        try:
            rows = _fetch(key, probe_day)
            result[key] = f"ok ({len(rows)}행)"
        except KRXAuthError:
            result[key] = "미신청(401)"
        except Exception as e:
            result[key] = f"오류: {type(e).__name__}"
    return result

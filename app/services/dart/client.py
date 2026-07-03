"""
DART OpenDART 공시 어댑터.

공시는 Gemini 검색으로 "발견"하는 데이터가 아니라 공식 API로 결정적으로 수집한다.
관심종목 분석 입력(확정 데이터) + 향후 이벤트 자동 트리거(공시 감지)의 공용 부품.

공통 원칙: 실패해도 분석이 죽지 않는다 — 예외를 밖으로 던지지 않고
{"available": False, "note": ...} 폴백을 반환, 호출부가 data_flags에 기록.

주의: DART는 종목코드가 아닌 자체 8자리 corp_code를 쓴다 (삼성전자 00126380).
corpCode.xml(ZIP) 전체 매핑을 파일 캐시하고 30일 주기로 갱신한다.
"""
import io
import json
import logging
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_BASE_URL = "https://opendart.fss.or.kr/api"
_VIEWER_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"
_CORP_CACHE_PATH = Path.home() / ".dart_corp_code.json"
_CORP_CACHE_TTL_DAYS = 30       # 정기 갱신 주기
_CORP_CACHE_MIN_AGE_HOURS = 24  # 미매핑 코드로 인한 강제 갱신 최소 간격 (재다운로드 폭주 방지)

_corp_map: dict[str, str] | None = None  # 인메모리 {stock_code(6자리): corp_code(8자리)}
_corp_map_fetched_at: datetime | None = None


def _download_corp_map(api_key: str) -> dict[str, str]:
    """corpCode.xml(ZIP) 다운로드 → {stock_code: corp_code}. 비상장(stock_code 빈값)은 제외."""
    resp = httpx.get(f"{_BASE_URL}/corpCode.xml",
                     params={"crtfc_key": api_key}, timeout=60)
    resp.raise_for_status()
    # 키 오류 시 ZIP 대신 에러 XML이 오면 BadZipFile → 호출부 폴백
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    root = ET.fromstring(zf.read(zf.namelist()[0]))
    mapping: dict[str, str] = {}
    for el in root.iter("list"):
        stock = (el.findtext("stock_code") or "").strip()
        corp = (el.findtext("corp_code") or "").strip()
        if stock and corp:
            mapping[stock] = corp
    if not mapping:
        raise ValueError("corpCode.xml 파싱 결과가 비어 있음")
    logger.info("DART corp_code 매핑 갱신: %d개 상장사", len(mapping))
    return mapping


def _get_corp_map(api_key: str, force: bool = False) -> dict[str, str]:
    """매핑 로드 — 인메모리 → 파일 캐시 → 다운로드 순. force여도 최소 간격 미만이면 캐시 유지."""
    global _corp_map, _corp_map_fetched_at
    now = datetime.now(timezone.utc)

    if _corp_map is None and _CORP_CACHE_PATH.exists():
        try:
            data = json.loads(_CORP_CACHE_PATH.read_text())
            _corp_map = data["map"]
            _corp_map_fetched_at = datetime.fromisoformat(data["fetched_at"])
        except Exception as e:
            logger.warning("DART corp_code 캐시 파일 손상 — 재다운로드: %s", e)

    age = (now - _corp_map_fetched_at) if _corp_map_fetched_at else None
    stale = age is None or age > timedelta(days=_CORP_CACHE_TTL_DAYS)
    refreshable = age is None or age > timedelta(hours=_CORP_CACHE_MIN_AGE_HOURS)

    if _corp_map is None or stale or (force and refreshable):
        _corp_map = _download_corp_map(api_key)
        _corp_map_fetched_at = now
        _CORP_CACHE_PATH.write_text(json.dumps(
            {"fetched_at": now.isoformat(), "map": _corp_map}))
    return _corp_map


def get_corp_code(stock_code: str) -> str | None:
    """티커(6자리) → DART corp_code. 미매핑이면 캐시 강제 갱신 1회 후 재시도 (신규상장 대응)."""
    api_key = (get_settings().dart_api_key or "").strip()
    if not api_key:
        return None
    mapping = _get_corp_map(api_key)
    if stock_code not in mapping:
        mapping = _get_corp_map(api_key, force=True)
    return mapping.get(stock_code)


def fetch_recent_disclosures(stock_code: str, end_date: date | None = None,
                             days: int = 14, limit: int = 20) -> dict:
    """최근 N일 공시 목록 — 스냅샷에 그대로 들어가는 확정 데이터.

    반환: {"available": True, "source": ..., "window": ..., "items": [...]}
          실패 시 {"available": False, "note": ...} (예외 없음)
    """
    api_key = (get_settings().dart_api_key or "").strip()
    if not api_key:
        return {"available": False, "note": "DART_API_KEY 미설정"}
    try:
        corp_code = get_corp_code(stock_code)
        if not corp_code:
            return {"available": False,
                    "note": f"DART corp_code 매핑 없음 ({stock_code}) — 비상장/신규상장 여부 확인"}

        end = end_date or date.today()
        bgn = end - timedelta(days=days)
        resp = httpx.get(f"{_BASE_URL}/list.json", params={
            "crtfc_key": api_key,
            "corp_code": corp_code,
            "bgn_de": bgn.strftime("%Y%m%d"),
            "end_de": end.strftime("%Y%m%d"),
            "page_count": 100,
        }, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        status = data.get("status")
        window = f"{bgn.isoformat()} ~ {end.isoformat()}"

        if status == "013":  # 조회된 데이터 없음 — 공시가 없는 것도 확정 사실
            return {"available": True, "source": "DART OpenDART list API",
                    "window": window, "items": [],
                    "note": "해당 기간 공시 없음 (공식 API 확인)"}
        if status != "000":
            return {"available": False,
                    "note": f"DART API 오류 status={status}: {data.get('message')}"}

        items = [{
            "date": it.get("rcept_dt"),
            "title": (it.get("report_nm") or "").strip(),
            "filer": it.get("flr_nm"),
            "url": _VIEWER_URL.format(rcept_no=it.get("rcept_no")),
        } for it in data.get("list", [])[:limit]]
        return {"available": True, "source": "DART OpenDART list API",
                "window": window, "items": items}
    except Exception as e:
        logger.warning("DART 공시 조회 실패 (%s): %s", stock_code, e)
        return {"available": False, "note": f"DART 조회 실패: {e}"}

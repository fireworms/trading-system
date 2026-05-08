"""
KOSPI 200 / KOSDAQ 150 지수 구성종목 근사치 조회.

KIS API 시가총액 순위 (FHPST01740000)를 섹터별로 순환 호출해
전 종목의 시가총액을 수집한 뒤 상위 200 / 150개를 반환.

업종코드 0001~0030: KOSPI / KRX 업종
업종코드 1001~1030: KOSDAQ 업종

캐시: lru_cache(maxsize=None) — 프로세스 재시작 시 갱신
주간 갱신: scheduler.py job_update_stock_master에서 refresh_index_cache() 호출
"""
import time
import logging
from functools import lru_cache

import httpx

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
# 섹터 코드 목록 (테스트로 확인된 유효 코드)
# ------------------------------------------------------------------ #

_KOSPI_SECTOR_CODES: list[str] = [
    "0001", "0003", "0004", "0005", "0006", "0007", "0008", "0009", "0010",
    "0011", "0012", "0013", "0014", "0015", "0016", "0017", "0018", "0019",
    "0020", "0021", "0024", "0025", "0026", "0028", "0029", "0030",
]

_KOSDAQ_SECTOR_CODES: list[str] = [
    "1001", "1006", "1007", "1009", "1010", "1011", "1013", "1014", "1015",
    "1016", "1018", "1019", "1020", "1021", "1023", "1024", "1025", "1026",
    "1027", "1029", "1030",
]

# ------------------------------------------------------------------ #
# 하드코딩 fallback (KIS API 실패 시)
# ------------------------------------------------------------------ #

_KOSPI200_FALLBACK: list[str] = [
    "005930", "000660", "207940", "373220", "005380", "000270", "035420",
    "068270", "051910", "005490", "055550", "035720", "105560", "012330",
    "086790", "003550", "034730", "006400", "028260", "032830", "316140",
    "096770", "024110", "000810", "017670", "030200", "329180", "009540",
    "042660", "010140", "066570", "009150", "018260", "042700", "093370",
    "011790", "003600", "326030", "000100", "128940", "185750", "069620",
    "302440", "403900", "036570", "259960", "251270", "011210", "064350",
    "204320", "161390", "241560", "005940", "006800", "071050", "088350",
    "005830", "010950", "078930", "267250", "047050", "032640", "139480",
    "004170", "069960", "033600", "097950", "000080", "003490", "020560",
    "011200", "000120", "012450", "047810", "015760", "036460", "034020",
    "000720", "047040", "006360", "028050", "004020", "402340",
]

_KOSDAQ150_FALLBACK: list[str] = [
    "247540", "086520", "196170", "091990", "145020", "214150", "357780",
    "039030", "140860", "348210", "041510", "035900", "122870", "095660",
    "112040", "263750", "293490", "277810", "000250", "086820", "067310",
    "091440", "095340", "065150", "058470", "078130", "009420", "060370",
    "035810", "214130",
]


# ------------------------------------------------------------------ #
# KIS API 호출
# ------------------------------------------------------------------ #

def _fetch_cap_rank_sector(client, iscd: str) -> list[tuple[str, int]]:
    """
    단일 섹터 시가총액 순위 조회.
    반환: [(stock_code, market_cap_억원), ...]
    """
    try:
        headers = client._headers("FHPST01740000")
        resp = httpx.get(
            f"{client._base}/uapi/domestic-stock/v1/quotations/volume-rank",
            headers=headers,
            params={
                "FID_COND_MRKT_DIV_CODE":  "J",
                "FID_COND_SCR_DIV_CODE":   "20174",
                "FID_INPUT_ISCD":          iscd,
                "FID_DIV_CLS_CODE":        "1",
                "FID_BLNG_CLS_CODE":       "0",
                "FID_TRGT_CLS_CODE":       "111111111",
                "FID_TRGT_EXLS_CLS_CODE":  "0000000000",
                "FID_INPUT_PRICE_1":       "0",
                "FID_INPUT_PRICE_2":       "0",
                "FID_VOL_CNT":             "0",
                "FID_INPUT_DATE_1":        "",
            },
            timeout=10,
        )
        resp.raise_for_status()
        rows = resp.json().get("output", []) or []
        result = []
        for r in rows:
            code = r.get("mksc_shrn_iscd", "").strip()
            cap  = r.get("stck_avls", "0").replace(",", "")
            if len(code) == 6 and code.isdigit():
                try:
                    result.append((code, int(cap or 0)))
                except ValueError:
                    result.append((code, 0))
        return result
    except Exception as e:
        logger.debug("Cap rank sector %s failed: %s", iscd, e)
        return []


def _collect_by_market(
    client,
    sector_codes: list[str],
    target_market: str,
    kr_stock_map: dict[str, str],  # code → market
    limit: int,
    sleep_sec: float = 0.15,
) -> list[str]:
    """
    섹터 순환 호출 → 시가총액 합산 → target_market 필터 → 상위 limit개.
    kr_stock_map: stock_master에서 로드한 {code: market} 맵
    """
    cap_map: dict[str, int] = {}

    for iscd in sector_codes:
        pairs = _fetch_cap_rank_sector(client, iscd)
        for code, cap in pairs:
            if code in cap_map:
                cap_map[code] = max(cap_map[code], cap)
            else:
                cap_map[code] = cap
        time.sleep(sleep_sec)

    # target_market 필터 (stock_master 기준)
    filtered = {
        code: cap
        for code, cap in cap_map.items()
        if kr_stock_map.get(code) == target_market
    }

    sorted_codes = sorted(filtered.keys(), key=lambda c: filtered[c], reverse=True)
    logger.info(
        "Index fetch %s: %d unique (pool %d), top5=%s",
        target_market, len(sorted_codes), len(cap_map), sorted_codes[:5],
    )
    return sorted_codes[:limit]


# ------------------------------------------------------------------ #
# 공개 API
# ------------------------------------------------------------------ #

@lru_cache(maxsize=None)
def _cached_index(market: str) -> tuple[str, ...]:
    """캐시: 프로세스 수명 동안 유지."""
    from app.core.database import SessionLocal
    from app.models.stock_master import StockMaster
    from app.services.kis.client import get_kis_client

    limit    = 200 if market == "KOSPI" else 150
    sectors  = _KOSPI_SECTOR_CODES if market == "KOSPI" else _KOSDAQ_SECTOR_CODES
    fallback = _KOSPI200_FALLBACK   if market == "KOSPI" else _KOSDAQ150_FALLBACK

    try:
        with SessionLocal() as db:
            client = get_kis_client(db)
            kr_map = {
                r.stock_code: r.market
                for r in db.query(StockMaster)
                .filter(StockMaster.country == "KR", StockMaster.is_active == True)
                .all()
            }

        codes = _collect_by_market(client, sectors, market, kr_map, limit)

        if len(codes) < limit // 2:   # 절반도 못 채우면 fallback
            logger.warning(
                "%s index fetch too few (%d < %d), using fallback",
                market, len(codes), limit // 2,
            )
            return tuple(fallback)

        return tuple(codes)

    except Exception as e:
        logger.error("Index constituent fetch failed for %s: %s", market, e)
        return tuple(fallback)


def get_kospi200() -> list[str]:
    """KOSPI 200 구성종목 코드 목록 (시가총액 기준 근사치)."""
    return list(_cached_index("KOSPI"))


def get_kosdaq150() -> list[str]:
    """KOSDAQ 150 구성종목 코드 목록 (시가총액 기준 근사치)."""
    return list(_cached_index("KOSDAQ"))


def refresh_index_cache() -> dict:
    """캐시 강제 갱신 (주간 스케줄러 호출용)."""
    _cached_index.cache_clear()
    k200  = get_kospi200()
    kq150 = get_kosdaq150()
    return {"KOSPI200": len(k200), "KOSDAQ150": len(kq150)}

"""
주식 마스터 데이터 관리.

stock_master 업데이트:
  - 코스피/코스닥: KIS .mst 파일 (288-byte 라인, \n 구분, CP949)
      [0:6]   단축코드, [9:21] ISIN, [21:61] 한글명
  - 나스닥: KIS nasmst.cod (탭 구분 텍스트)
      필드4=심볼, 필드6=한글명, 필드7=영문명

candidate_stocks 선별:
  - KIS 시가총액/거래량 순위 API → stock_master 교차 필터링
  - KOSPI 200 + KOSDAQ 150 + NAS 200
"""
import io
import logging
import zipfile
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models.stock_master import StockMaster
from app.models.candidate_stock import CandidateStock

logger = logging.getLogger(__name__)

_MST_URLS = {
    "KOSPI":  "https://new.real.download.dws.co.kr/common/master/kospi_code.mst.zip",
    "KOSDAQ": "https://new.real.download.dws.co.kr/common/master/kosdaq_code.mst.zip",
    "NAS":    "https://new.real.download.dws.co.kr/common/master/nasmst.cod.zip",
}

# ------------------------------------------------------------------ #
# 다운로드
# ------------------------------------------------------------------ #

def _download_zip(url: str, timeout: int = 60) -> bytes:
    resp = httpx.get(url, timeout=timeout, follow_redirects=True)
    resp.raise_for_status()
    return resp.content


def _extract_first_file(zip_bytes: bytes) -> bytes:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        name = zf.namelist()[0]
        return zf.read(name)


# ------------------------------------------------------------------ #
# 파서
# ------------------------------------------------------------------ #

def _parse_kr_mst(data: bytes, market: str) -> list[dict]:
    """
    KOSPI/KOSDAQ .mst 파싱.
    라인 구조 (288바이트 + \\n):
      [0:6]   단축코드 (6자리 숫자)
      [9:21]  ISIN (12자)
      [21:61] 한글종목명 (40바이트, CP949)
      [61:63] 종목타입 (ST=주식, EF=ETF, RT=ETN, MF/IF=펀드)
    ST 타입만 포함 (개별 주식). ETF/ETN/펀드 제외.
    """
    seen: set[str] = set()
    results = []
    skipped_etf = 0
    for line in data.split(b"\n"):
        if len(line) < 65:
            continue
        try:
            code      = line[0:6].decode("cp949").strip()
            name      = line[21:61].decode("cp949").strip()
            type_code = line[61:63].decode("cp949").strip()
            if not code or not name:
                continue
            if not code.isdigit() or len(code) != 6:
                continue
            if type_code != "ST":   # ETF(EF), ETN(RT), 펀드(MF/IF) 제외
                skipped_etf += 1
                continue
            if code in seen:
                continue
            seen.add(code)
            results.append({
                "stock_code": code,
                "stock_name": name,
                "market":     market,
                "country":    "KR",
            })
        except Exception:
            continue

    logger.info("Parsed %s: %d stocks (%d ETF/기타 제외)", market, len(results), skipped_etf)
    return results


def _parse_nas_cod(data: bytes) -> list[dict]:
    """
    nasmst.cod 파싱 (탭 구분).
    필드: country\\texchange_no\\texchange\\t거래소명\\tsymbol\\tNASsymbol\\t한글명\\t영문명\\t...
    """
    try:
        text = data.decode("cp949")
    except Exception:
        text = data.decode("latin-1", errors="replace")

    seen: set[str] = set()
    results = []
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        symbol   = parts[4].strip()
        kor_name = parts[6].strip()
        eng_name = parts[7].strip() if len(parts) > 7 else ""
        name     = kor_name or eng_name
        if not symbol or not name:
            continue
        if len(symbol) > 10:
            continue
        if symbol in seen:
            continue
        seen.add(symbol)
        results.append({
            "stock_code": symbol,
            "stock_name": name,
            "market":     "NAS",
            "country":    "US",
        })

    logger.info("Parsed NAS: %d records", len(results))
    return results


# ------------------------------------------------------------------ #
# DB Upsert
# ------------------------------------------------------------------ #

def _upsert_stock_master(db: Session, records: list[dict], market: str) -> tuple[int, int]:
    """해당 시장의 기존 레코드 이름 갱신, 없으면 추가. (created, updated) 반환."""
    now = datetime.now(timezone.utc)

    existing: dict[str, StockMaster] = {
        r.stock_code: r
        for r in db.scalars(
            select(StockMaster).where(StockMaster.market == market)
        ).all()
    }

    created = updated = 0
    for rec in records:
        code = rec["stock_code"]
        if code in existing:
            row = existing[code]
            if row.stock_name != rec["stock_name"]:
                row.stock_name = rec["stock_name"]
                row.updated_at = now
                updated += 1
        else:
            db.add(StockMaster(
                stock_code = code,
                stock_name = rec["stock_name"],
                market     = rec["market"],
                country    = rec["country"],
                is_active  = True,
                updated_at = now,
            ))
            created += 1

    db.commit()
    return created, updated


# ------------------------------------------------------------------ #
# 공개 함수: stock_master 전체 업데이트
# ------------------------------------------------------------------ #

def update_stock_master(db: Session) -> dict:
    """
    KIS MST 파일 다운로드 → 파싱 → DB upsert.
    시장별로 독립 트랜잭션 사용 (실패해도 다음 시장 진행).
    반환: { "KOSPI": {"created":N,"updated":N}, ... }
    """
    from app.core.database import SessionLocal

    results = {}
    for market, url in _MST_URLS.items():
        try:
            logger.info("Downloading %s master from %s", market, url)
            raw  = _download_zip(url)
            data = _extract_first_file(raw)

            if market in ("KOSPI", "KOSDAQ"):
                records = _parse_kr_mst(data, market)
            else:
                records = _parse_nas_cod(data)

            if not records:
                logger.warning("%s: No records parsed", market)
                results[market] = {"created": 0, "updated": 0, "error": "parse_empty"}
                continue

            # 시장별 독립 세션으로 upsert (한 시장 실패가 다른 시장에 영향 없도록)
            with SessionLocal() as sess:
                c, u = _upsert_stock_master(sess, records, market)

            results[market] = {"created": c, "updated": u, "total": len(records)}
            logger.info("%s: created=%d updated=%d total=%d", market, c, u, len(records))

        except Exception as e:
            logger.error("stock_master update failed for %s: %s", market, e)
            results[market] = {"error": str(e)}

    return results


# ------------------------------------------------------------------ #
# 후보 선별 설정
# ------------------------------------------------------------------ #

_CANDIDATE_LIMITS = {"KOSPI": 200, "KOSDAQ": 150, "NAS": 200}

def _get_core_list(market: str) -> list[str]:
    """
    candidate_stocks 보완용 핵심 종목 목록.
    KOSPI200 / KOSDAQ150 구성종목 기반 (index_constituents 모듈).
    캐시 덕분에 반복 호출 시 API 재조회 없음.
    """
    from app.services.stock_master.index_constituents import get_kospi200, get_kosdaq150
    return get_kospi200() if market == "KOSPI" else get_kosdaq150()


# ------------------------------------------------------------------ #
# 거래량 순위 페이지네이션
# ------------------------------------------------------------------ #

def _fetch_volume_rank_paginated(client, max_pages: int = 10) -> list[str]:
    """
    KIS 거래량 순위 API 페이지네이션 호출.
    tr_cont 헤더 + CTX_AREA 파라미터로 연속 조회.
    최대 max_pages 페이지 시도, 중단 조건 만족 시 조기 종료.
    실패 시 빈 리스트.
    """
    import httpx as _httpx

    url      = f"{client._base}/uapi/domestic-stock/v1/quotations/volume-rank"
    tr_id    = "FHPST01710000"
    all_codes: list[str] = []
    seen:      set[str]  = set()
    ctx_fk   = ""
    ctx_nk   = ""

    for page in range(max_pages):
        headers = client._headers(tr_id)
        if page > 0:
            headers["tr_cont"] = "N"

        params = {
            "FID_COND_MRKT_DIV_CODE":  "J",
            "FID_COND_SCR_DIV_CODE":   "20171",
            "FID_INPUT_ISCD":          "0000",
            "FID_DIV_CLS_CODE":        "1",        # 보통주만
            "FID_BLNG_CLS_CODE":       "0",        # 평균거래량 기준
            "FID_TRGT_CLS_CODE":       "111111111",
            "FID_TRGT_EXLS_CLS_CODE":  "0000000000",
            "FID_INPUT_PRICE_1":       "0",
            "FID_INPUT_PRICE_2":       "0",
            "FID_VOL_CNT":             "0",
            "FID_INPUT_DATE_1":        "",
            "CTX_AREA_FK100":          ctx_fk,
            "CTX_AREA_NK100":          ctx_nk,
        }

        try:
            resp = _httpx.get(url, headers=headers, params=params, timeout=10)
            resp.raise_for_status()
        except Exception as e:
            logger.warning("Volume rank page %d failed: %s", page + 1, e)
            break

        data = resp.json()
        if data.get("rt_cd") not in ("0", None):
            logger.warning("Volume rank API error: %s", data.get("msg1"))
            break

        rows       = data.get("output", [])
        page_codes = [
            r.get("mksc_shrn_iscd", "").strip() for r in rows
        ]
        page_codes = [c for c in page_codes if len(c) == 6 and c.isdigit() and c not in seen]
        for c in page_codes:
            seen.add(c)
        all_codes.extend(page_codes)

        tr_cont_hdr = resp.headers.get("tr_cont", "D").strip()
        ctx_fk      = data.get("ctx_area_fk100", "").strip()
        ctx_nk      = data.get("ctx_area_nk100", "").strip()

        logger.info("Volume rank page %d: +%d codes (total %d), tr_cont=%s",
                    page + 1, len(page_codes), len(all_codes), tr_cont_hdr)

        # 종료 조건: 더 이상 데이터 없거나 ctx 없음
        if tr_cont_hdr in ("D", "E", "") or (not ctx_fk and not ctx_nk):
            break

    logger.info("Volume rank paginated total: %d codes", len(all_codes))
    return all_codes


# ------------------------------------------------------------------ #
# fallback: stride 균등 샘플링
# ------------------------------------------------------------------ #

def _stride_sample(rows: list, limit: int) -> list:
    """정렬된 목록에서 균등 간격으로 limit개 추출."""
    total = len(rows)
    if total <= limit:
        return rows
    stride = total / limit
    return [rows[int(i * stride)] for i in range(limit)]


def _fallback_stride(db: Session, market: str, limit: int) -> list[StockMaster]:
    all_rows = (
        db.query(StockMaster)
        .filter(StockMaster.market == market, StockMaster.is_active == True)
        .order_by(StockMaster.stock_code)
        .all()
    )
    return _stride_sample(all_rows, limit)


# ------------------------------------------------------------------ #
# 시장별 최종 선별
# ------------------------------------------------------------------ #

def _select_market_candidates(
    market: str,
    limit: int,
    ranked_codes: list[str],        # 거래량 순위 (전체 KRX)
    kr_stocks: dict[str, StockMaster],
    core_list: list[str],
) -> list[StockMaster]:
    """
    선별 우선순위:
      1) 거래량순위 API 결과 (해당 시장 개별주식)
      2) 핵심 대형주 보완 (아직 미포함인 것만)
      3) stride 균등 샘플링으로 나머지 채우기
    """
    selected:      list[StockMaster] = []
    selected_codes: set[str]         = set()

    # 1) API 거래량 순위 (시장 필터)
    for code in ranked_codes:
        if len(selected) >= limit:
            break
        r = kr_stocks.get(code)
        if r and r.market == market and code not in selected_codes:
            selected.append(r)
            selected_codes.add(code)

    n_ranked = len(selected)

    # 2) 핵심 대형주 보완
    for code in core_list:
        if len(selected) >= limit:
            break
        r = kr_stocks.get(code)
        if r and r.market == market and code not in selected_codes:
            selected.append(r)
            selected_codes.add(code)

    n_core = len(selected) - n_ranked

    # 3) stride 샘플링으로 나머지 채우기
    remaining = limit - len(selected)
    if remaining > 0:
        pool = sorted(
            [r for r in kr_stocks.values()
             if r.market == market and r.stock_code not in selected_codes],
            key=lambda r: r.stock_code,
        )
        selected.extend(_stride_sample(pool, remaining))

    n_stride = len(selected) - n_ranked - n_core
    logger.info("%s: %d ranked + %d core + %d stride = %d",
                market, n_ranked, n_core, n_stride, len(selected))
    return selected[:limit]


# ------------------------------------------------------------------ #
# 공개 함수: candidate_stocks 자동 선별
# ------------------------------------------------------------------ #

def refresh_candidate_stocks(db: Session) -> dict:
    """
    1) KIS 거래량순위 페이지네이션 → 최대한 확보
    2) KOSPI: 순위 + 핵심 대형주 보완 + stride 채우기
    3) KOSDAQ: 거래량 위주 + 최소 핵심 보완 + stride 채우기
    4) NAS: stride 균등 샘플링
    반환: { "KOSPI": N, "KOSDAQ": N, "NAS": N }
    """
    from app.services.kis.client import get_kis_client

    client = get_kis_client(db)

    # 거래량 순위 (페이지네이션)
    ranked_codes = _fetch_volume_rank_paginated(client, max_pages=10)

    # stock_master KR 전체 로드
    kr_stocks: dict[str, StockMaster] = {
        r.stock_code: r
        for r in db.query(StockMaster)
        .filter(StockMaster.market.in_(["KOSPI", "KOSDAQ"]), StockMaster.is_active == True)
        .all()
    }

    now    = datetime.now(timezone.utc)
    counts = {}

    # 기존 후보 전체 비활성화
    db.query(CandidateStock).update({"is_active": False})
    db.flush()

    for market, limit in _CANDIDATE_LIMITS.items():
        if market == "NAS":
            top_rows = _fallback_stride(db, "NAS", limit)
            selected = [(r.stock_code, r.stock_name, r.market, r.sector, "US") for r in top_rows]
        else:
            core = _get_core_list(market)
            top_rows = _select_market_candidates(
                market, limit, ranked_codes, kr_stocks, core
            )
            selected = [(r.stock_code, r.stock_name, r.market, r.sector, "KR") for r in top_rows]

        # candidate_stocks upsert
        existing_map = {
            r.stock_code: r
            for r in db.query(CandidateStock)
            .filter(CandidateStock.stock_code.in_([s[0] for s in selected]))
            .all()
        }
        for code, name, mkt, sector, country in selected:
            if code in existing_map:
                row            = existing_map[code]
                row.stock_name = name
                row.market     = mkt
                row.sector     = sector
                row.country    = country
                row.is_active  = True
            else:
                db.add(CandidateStock(
                    stock_code = code,
                    stock_name = name,
                    market     = mkt,
                    sector     = sector,
                    country    = country,
                    is_active  = True,
                    added_at   = now,
                ))

        counts[market] = len(selected)
        logger.info("Candidates %s: %d", market, len(selected))

    db.commit()
    return counts

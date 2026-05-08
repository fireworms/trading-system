"""
주식 마스터 데이터 관리.

stock_master 업데이트:
  - 코스피/코스닥: KIS .mst 파일 (288-byte 라인, \n 구분, CP949)
      [0:6]   단축코드, [9:21] ISIN, [21:61] 한글명
  - 나스닥: KIS nasmst.cod (탭 구분 텍스트)
      필드4=심볼, 필드6=한글명, 필드7=영문명
"""
import io
import logging
import zipfile
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models.stock_master import StockMaster

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

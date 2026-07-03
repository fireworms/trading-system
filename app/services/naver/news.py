"""
네이버 뉴스 검색 어댑터.

관심종목 분석의 뉴스 최신성이 Gemini 검색 그라운딩의 운에 걸리지 않도록,
최신순 정렬 기사 목록을 결정적으로 수집해 스냅샷에 넣는다 (pubDate 구조화 — 날짜 신뢰 가능).
Gemini 검색은 이 목록의 내용 확인·보강 담당으로 역할 축소.

공통 원칙: 실패해도 분석이 죽지 않는다 — {"available": False, "note": ...} 폴백 반환.
"""
import html
import logging
import re
from datetime import datetime, timezone

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_API_URL = "https://openapi.naver.com/v1/search/news.json"
_TAG_RE = re.compile(r"<[^>]+>")


def _clean_title(raw: str) -> str:
    """검색 API 제목의 <b> 태그·HTML 엔티티 제거."""
    return html.unescape(_TAG_RE.sub("", raw or "")).strip()


def _norm_key(title: str) -> str:
    """중복 판정 키 — 공백/문장부호 차이만 다른 전재 기사 제거용."""
    return re.sub(r"[\s\W]+", "", title).lower()


def _parse_pubdate(raw: str) -> str | None:
    """RFC822(pubDate) → 'YYYY-MM-DD'. 파싱 불가면 None (가짜 날짜 금지)."""
    try:
        return datetime.strptime(raw, "%a, %d %b %Y %H:%M:%S %z").date().isoformat()
    except (ValueError, TypeError):
        return None


def fetch_recent_news(query: str, top_n: int = 10) -> dict:
    """종목명 최신 기사 top_n건 — 제목 중복 제거 후 제목/날짜/링크.

    반환: {"available": True, "source": ..., "items": [...]}
          실패 시 {"available": False, "note": ...} (예외 없음)
    """
    settings = get_settings()
    cid = (settings.naver_client_id or "").strip()
    csec = (settings.naver_client_secret or "").strip()
    if not cid or not csec:
        return {"available": False, "note": "NAVER_CLIENT_ID/SECRET 미설정"}
    try:
        resp = httpx.get(_API_URL,
                         params={"query": query, "display": 30, "sort": "date"},
                         headers={"X-Naver-Client-Id": cid,
                                  "X-Naver-Client-Secret": csec},
                         timeout=15)
        resp.raise_for_status()
        seen: set[str] = set()
        items = []
        for it in resp.json().get("items", []):
            title = _clean_title(it.get("title", ""))
            key = _norm_key(title)
            if not title or key in seen:
                continue
            seen.add(key)
            items.append({
                "title": title,
                "date": _parse_pubdate(it.get("pubDate", "")),
                "link": it.get("originallink") or it.get("link"),
            })
            if len(items) >= top_n:
                break
        return {"available": True,
                "source": "Naver News Search API (최신순)",
                "as_of": datetime.now(timezone.utc).isoformat(),
                "query": query,
                "items": items}
    except Exception as e:
        logger.warning("네이버 뉴스 조회 실패 (%s): %s", query, e)
        return {"available": False, "note": f"네이버 뉴스 조회 실패: {e}"}

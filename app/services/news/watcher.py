"""
장중 뉴스 감시 서비스.
Gemini 그라운딩으로 주가 급변 이슈 감지.
감지된 이벤트는 news_events 테이블에 누적되고,
사후 시장 영향(KOSPI/KOSDAQ 변화율)이 검증되어 AI 판단 기준에 재사용된다.
"""
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal

logger = logging.getLogger(__name__)

NEWS_MODEL = "gemini-2.5-flash"

_BASE_PROMPT = """
당신은 한국 주식시장 리스크 감시 전문가입니다.
지금 시각 기준으로 한국 주식시장에 즉각적인 대규모 영향을 줄 수 있는 신규 이슈가 있는지 판단하세요.

감지 대상:
- 글로벌: 전쟁 발발/확전, 핵 위협, 글로벌 금융위기, 주요국 경제 봉쇄, 대형 테러, 주요 중앙은행 긴급 금리 결정
- 한국 국내 정치·정책: 대통령·정부의 증시 직접 영향 발언(국민배당금·주식과세·공매도 제도 변경), 부동산 규제 급변, 재벌·대기업 규제 강화, 기업 밸류업 프로그램 중단·변경, 계엄/탄핵 등 헌정 충격, 여야 합의 없는 대규모 추경·재정 확장 선언
제외 대상: 이미 시장에 반영된 만성적 리스크, 일반 경기 지표, 예정된 이벤트, 선거 관련 일반 공약(집권 후 실제 입법 전).

{history_context}

위 히스토리를 참고하여 현재 이슈를 판단하세요.
과거에 비슷한 수준의 이슈가 실제로 시장에 미친 영향이 미미했다면 NORMAL로 판단하세요.

다음 JSON만 반환하세요:
{{
  "has_major_event": true/false,
  "severity": "NORMAL" or "WARNING" or "CRITICAL",
  "event_description": "이슈 요약 (없으면 빈 문자열)",
  "keywords": ["핵심키워드1", "핵심키워드2"],
  "ai_confidence": 0.0~1.0
}}
"""


def _build_history_context(db) -> str:
    """최근 뉴스 이벤트 + 실제 시장 영향을 컨텍스트 문자열로 반환."""
    from sqlalchemy import select
    from app.models.news_event import NewsEvent

    events = db.scalars(
        select(NewsEvent)
        .order_by(NewsEvent.detected_at.desc())
        .limit(15)
    ).all()

    if not events:
        return ""

    lines = ["=== 최근 뉴스 이벤트 히스토리 (AI 판단 보정용) ==="]
    for ev in reversed(events):
        date_str = ev.detected_at.strftime("%Y-%m-%d %H:%M")
        impact = ""
        if ev.kospi_change_1d is not None:
            impact = f" → 실제 KOSPI 1일:{ev.kospi_change_1d:+.2f}%"
        if ev.kospi_change_3d is not None:
            impact += f" 3일:{ev.kospi_change_3d:+.2f}%"
        if not impact:
            impact = " → 시장 영향 검증 대기 중"
        lines.append(f"[{date_str}] {ev.severity}: \"{ev.event_description[:60]}\"{impact}")

    lines.append("")
    return "\n".join(lines)


def check_news(db=None) -> dict:
    """
    뉴스 감시 1회 실행.
    db가 있으면 히스토리 컨텍스트를 프롬프트에 주입.
    반환: {has_major_event, severity, event_description, keywords, ai_confidence}
    """
    from google import genai
    from google.genai import types
    from app.core.config import get_settings

    api_key = get_settings().gemini_api_key
    if not api_key:
        logger.warning("GEMINI_API_KEY not set, skipping news check")
        return {"has_major_event": False, "severity": "NORMAL", "event_description": "",
                "keywords": [], "ai_confidence": 0.0}

    history_context = _build_history_context(db) if db else ""
    prompt = _BASE_PROMPT.format(history_context=history_context)

    client = genai.Client(api_key=api_key)
    config = types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())]
    )

    try:
        response = client.models.generate_content(
            model=NEWS_MODEL, contents=prompt, config=config,
        )
        text = response.text.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        result = json.loads(text)
        return {
            "has_major_event": bool(result.get("has_major_event", False)),
            "severity":         result.get("severity", "NORMAL"),
            "event_description": result.get("event_description", ""),
            "keywords":         result.get("keywords", []),
            "ai_confidence":    float(result.get("ai_confidence", 0.5)),
        }
    except Exception as e:
        logger.error("News check failed: %s", e)
        return {"has_major_event": False, "severity": "NORMAL", "event_description": "",
                "keywords": [], "ai_confidence": 0.0}


def _get_index_levels(db) -> tuple[Decimal | None, Decimal | None]:
    """현재 KOSPI/KOSDAQ 지수 레벨 조회."""
    try:
        from app.services.kis.client import get_kis_client
        client = get_kis_client(db)
        kospi  = client._get_index_level("0001")
        kosdaq = client._get_index_level("1001")
        return kospi, kosdaq
    except Exception as e:
        logger.warning("Index level fetch failed: %s", e)
        return None, None


def run_news_check_and_act() -> None:
    """
    뉴스 체크 → DB 저장 → WARNING이면 자동매매 중단 + 텔레그램.
    스케줄러에서 호출.
    """
    from app.core.database import SessionLocal
    from app.core.config_store import get_config, set_config
    from app.models.news_event import NewsEvent, NewsSeverity

    with SessionLocal() as db:
        # 사용량 카운터 갱신
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if get_config(db, "news_today_date", "") != today:
            set_config(db, "news_today_date", today)
            set_config(db, "news_today_usage", "0")
        usage = int(get_config(db, "news_today_usage", "0")) + 1
        set_config(db, "news_today_usage", str(usage))
        set_config(db, "news_last_check_at", datetime.now(timezone.utc).isoformat())

        result = check_news(db)
        logger.info("News check result: %s", result)

        # 현재 지수 레벨 저장
        kospi_level, kosdaq_level = _get_index_levels(db)

        # news_events 저장 (NORMAL 포함 모두 기록)
        event = NewsEvent(
            severity          = NewsSeverity(result["severity"]),
            event_description = result["event_description"],
            keywords          = result["keywords"],
            ai_confidence     = Decimal(str(result["ai_confidence"])),
            kospi_at_detection  = kospi_level,
            kosdaq_at_detection = kosdaq_level,
        )
        db.add(event)

        if result["severity"] == "WARNING":
            set_config(db, "news_auto_trade_paused", "true")
            set_config(db, "news_pause_reason", result["event_description"])

        db.commit()

    if result["severity"] == "WARNING":
        try:
            from app.services.telegram.notifier import notify_admins_error
            notify_admins_error(
                "⚠️ 뉴스 감시 — 자동매매 일시 중단",
                f"{result['event_description']}\n\n신규 매수가 중단됐습니다. 상황 확인 후 수동으로 재개해주세요.",
            )
        except Exception as e:
            logger.error("Telegram alert failed: %s", e)


def verify_news_events() -> None:
    """
    1일/3일 경과 뉴스 이벤트의 실제 시장 영향 검증.
    스케줄러에서 매일 호출.
    """
    from datetime import timedelta
    from sqlalchemy import select
    from app.core.database import SessionLocal
    from app.models.news_event import NewsEvent
    from app.services.kis.client import get_kis_client

    with SessionLocal() as db:
        now = datetime.now(timezone.utc)

        try:
            client = get_kis_client(db)
            kospi_now  = client._get_index_level("0001")
            kosdaq_now = client._get_index_level("1001")
        except Exception as e:
            logger.warning("Index fetch failed for verification: %s", e)
            return

        if not kospi_now or not kosdaq_now:
            return

        # 1일 경과 미검증
        events_1d = db.scalars(
            select(NewsEvent).where(
                NewsEvent.verified_1d_at == None,
                NewsEvent.kospi_at_detection != None,
                NewsEvent.detected_at <= now - timedelta(days=1),
            )
        ).all()

        for ev in events_1d:
            if ev.kospi_at_detection and ev.kospi_at_detection > 0:
                ev.kospi_change_1d  = ((kospi_now  - ev.kospi_at_detection)  / ev.kospi_at_detection  * 100).quantize(Decimal("0.0001"))
            if ev.kosdaq_at_detection and ev.kosdaq_at_detection > 0:
                ev.kosdaq_change_1d = ((kosdaq_now - ev.kosdaq_at_detection) / ev.kosdaq_at_detection * 100).quantize(Decimal("0.0001"))
            ev.verified_1d_at = now
            logger.info("Verified 1d: event=%s KOSPI=%s%%", ev.event_id, ev.kospi_change_1d)

        # 3일 경과 미검증
        events_3d = db.scalars(
            select(NewsEvent).where(
                NewsEvent.verified_3d_at == None,
                NewsEvent.kospi_at_detection != None,
                NewsEvent.detected_at <= now - timedelta(days=3),
            )
        ).all()

        for ev in events_3d:
            if ev.kospi_at_detection and ev.kospi_at_detection > 0:
                ev.kospi_change_3d  = ((kospi_now  - ev.kospi_at_detection)  / ev.kospi_at_detection  * 100).quantize(Decimal("0.0001"))
            if ev.kosdaq_at_detection and ev.kosdaq_at_detection > 0:
                ev.kosdaq_change_3d = ((kosdaq_now - ev.kosdaq_at_detection) / ev.kosdaq_at_detection * 100).quantize(Decimal("0.0001"))
            ev.verified_3d_at = now
            logger.info("Verified 3d: event=%s KOSPI=%s%%", ev.event_id, ev.kospi_change_3d)

        db.commit()
        logger.info("News verification done: 1d=%d, 3d=%d", len(events_1d), len(events_3d))

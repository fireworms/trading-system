"""
장중 뉴스 감시 서비스.
Gemini 그라운딩으로 주가 급변 이슈(전쟁, 금융위기 등) 감지 시
자동매매를 일시 중단하고 텔레그램으로 알림 전송.
"""
import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

NEWS_MODEL = "gemini-2.5-flash"

PROMPT = """
지금 시각 기준으로 한국 주식시장에 즉각적인 대규모 영향을 줄 수 있는 사건이 발생했는지 확인해줘.
대상 이슈: 전쟁 발발/확전, 핵 위협, 글로벌 금융위기, 주요국 경제 봉쇄, 대형 테러, 주요 중앙은행 긴급 금리 결정 등.
단순 경기 지표 발표나 일반 뉴스는 포함하지 마.

반드시 아래 JSON만 반환해:
{
  "has_major_event": true/false,
  "severity": "NORMAL" or "WARNING",
  "event_description": "이슈 요약 (없으면 빈 문자열)"
}
"""


def check_news() -> dict:
    """
    뉴스 감시 1회 실행.
    반환: {"has_major_event": bool, "severity": str, "event_description": str}
    """
    import os
    import google.generativeai as genai

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        logger.warning("GEMINI_API_KEY not set, skipping news check")
        return {"has_major_event": False, "severity": "NORMAL", "event_description": ""}

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        NEWS_MODEL,
        tools="google_search_retrieval",
    )

    try:
        response = model.generate_content(PROMPT)
        text = response.text.strip()
        # JSON 블록 추출
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        result = json.loads(text)
        return {
            "has_major_event": bool(result.get("has_major_event", False)),
            "severity": result.get("severity", "NORMAL"),
            "event_description": result.get("event_description", ""),
        }
    except Exception as e:
        logger.error("News check failed: %s", e)
        return {"has_major_event": False, "severity": "NORMAL", "event_description": ""}


def run_news_check_and_act() -> None:
    """
    뉴스 체크 후 WARNING이면 자동매매 중단 + 텔레그램 알림.
    스케줄러에서 호출.
    """
    from app.core.database import SessionLocal
    from app.core.config_store import get_config, set_config

    with SessionLocal() as db:
        # 오늘 사용량 카운터 갱신
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        usage_date = get_config(db, "news_today_date", "")
        if usage_date != today:
            set_config(db, "news_today_date", today)
            set_config(db, "news_today_usage", "0")
        usage = int(get_config(db, "news_today_usage", "0")) + 1
        set_config(db, "news_today_usage", str(usage))
        set_config(db, "news_last_check_at", datetime.now(timezone.utc).isoformat())

    result = check_news()
    logger.info("News check result: %s", result)

    if result["severity"] == "WARNING":
        with SessionLocal() as db:
            from app.core.config_store import set_config as sc
            sc(db, "news_auto_trade_paused", "true")
            sc(db, "news_pause_reason", result["event_description"])

        # 텔레그램 어드민 알림
        try:
            from app.services.telegram.notifier import notify_admins_error
            notify_admins_error(
                "⚠️ 뉴스 감시 — 자동매매 일시 중단",
                f"{result['event_description']}\n\n신규 매수가 중단됐습니다. 상황 확인 후 수동으로 재개해주세요.",
            )
        except Exception as e:
            logger.error("Telegram alert failed: %s", e)

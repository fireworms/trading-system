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


_MORNING_GATE_PROMPT = """
당신은 한국 주식시장 개장 전 리스크 감시 전문가입니다.
지금은 한국 주식시장 개장(09:00 KST) 전 아침입니다.

어젯밤 미국 시장 마감부터 지금까지 한국 주식시장 전반에 중대한 영향을 줄 수 있는 이슈가 있는지 판단하세요.

주요 체크 항목:
- 미국 증시: S&P500/나스닥 야간 등락률 (±2% 이상이면 주목)
- 미국/글로벌 선물: 현재 S&P500·나스닥·VIX 선물 상태
- 지정학: 전쟁 확전, 대형 테러, 핵 위협 등 야간 급변 사항
- 글로벌 금융: 주요국 긴급 금리 결정, 대형 금융기관 위기 징후
- 한국 관련: 야간 원/달러 환율 급변 (+2% 이상), 한국 관련 국제 이슈

판단 기준:
- NORMAL: 이상 없음 → 정상 매수 진행
- WARNING: 미국 선물 -1.5% 이상 또는 중대 지정학 이슈 → 당일 매수 자제
- CRITICAL: 미국 선물 -3% 이상 또는 시스템 충격 수준 이슈 → 당일 매수 중단

다음 JSON만 반환하세요:
{{
  "severity": "NORMAL" or "WARNING" or "CRITICAL",
  "reason": "판단 근거 1~2문장 (NORMAL이면 빈 문자열)",
  "us_futures_pct": -1.5,
  "ai_confidence": 0.0~1.0
}}
"""


def morning_gate_check() -> None:
    """
    08:00 개장 전 리스크 체크.
    WARNING/CRITICAL 감지 시 morning_gate_paused=true 설정 → 09:20 매수 잡이 스킵.
    매일 아침 자동 리셋 후 새로 체크한다.
    """
    from google import genai
    from google.genai import types
    from app.core.config import get_settings
    from app.core.config_store import get_config, set_config
    from app.core.database import SessionLocal

    api_key = get_settings().gemini_api_key
    if not api_key:
        logger.warning("GEMINI_API_KEY not set, skipping morning gate check")
        return

    with SessionLocal() as db:
        # 매일 아침 리셋 — 전날 플래그 초기화
        set_config(db, "morning_gate_paused", "false")
        set_config(db, "morning_gate_reason", "")

        client_g = genai.Client(api_key=api_key)
        config = types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())]
        )

        try:
            response = client_g.models.generate_content(
                model=NEWS_MODEL, contents=_MORNING_GATE_PROMPT, config=config,
            )
            text = response.text.strip()
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            result = json.loads(text)
            severity = result.get("severity", "NORMAL")
            reason   = result.get("reason", "")
            logger.info("Morning gate check: severity=%s reason=%s", severity, reason)
        except Exception as e:
            logger.error("Morning gate check failed: %s", e)
            return

        if severity in ("WARNING", "CRITICAL"):
            set_config(db, "morning_gate_paused", "true")
            set_config(db, "morning_gate_reason", f"[{severity}] {reason}")
            logger.warning("Morning gate PAUSED: %s — %s", severity, reason)

            from app.services.telegram.notifier import notify_admins_error
            notify_admins_error(
                f"🌅 모닝 게이트 {severity}",
                f"{reason}\n오늘 09:20 자동매수가 차단됩니다.",
            )
        else:
            logger.info("Morning gate: NORMAL, auto trade proceeds")


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

        # 듀얼 시그널 조치: AI severity + 실시간 KOSPI 등락률 교차 검증
        if result["severity"] in ("WARNING", "CRITICAL"):
            _apply_dual_signal_action(db, result)

    if result["severity"] == "WARNING":
        try:
            from app.services.telegram.notifier import notify_admins_error
            notify_admins_error(
                "⚠️ 뉴스 감시 — 자동매매 일시 중단",
                f"{result['event_description']}\n\n신규 매수가 중단됐습니다. 상황 확인 후 수동으로 재개해주세요.",
            )
        except Exception as e:
            logger.error("Telegram alert failed: %s", e)


def _apply_dual_signal_action(db, news_result: dict) -> None:
    """
    AI severity + 실시간 KOSPI 등락률 교차 검증 후 조치.
    - CRITICAL + KOSPI -2% 이하 → 전 포지션 긴급 청산
    - WARNING/CRITICAL + KOSPI -1% 이하 → 수익 포지션 손절선 강화
    모델 오탐 방지: AI 단독 신호로는 청산/손절 강화 미발동.
    """
    from app.services.kis.client import get_kis_client
    from app.services.trading.executor import TradeExecutor
    from app.services.telegram.notifier import notify_admins_error

    try:
        client = get_kis_client(db)
        market = client.get_index_change_pct()
        kospi_chg = float(market.get("KOSPI", 0))
    except Exception as e:
        logger.warning("Dual signal: KOSPI change fetch failed: %s", e)
        return

    severity = news_result["severity"]
    reason   = news_result["event_description"]
    executor = TradeExecutor(db)

    if severity == "CRITICAL" and kospi_chg <= -2.0:
        logger.warning("Dual signal CRITICAL+KOSPI%.1f%% → emergency close all", kospi_chg)
        closed = executor.emergency_close_all_positions(reason=f"[CRITICAL] {reason}")
        try:
            notify_admins_error(
                "🚨 뉴스 긴급 청산",
                f"{reason}\n\nKOSPI {kospi_chg:+.1f}% — {closed}개 포지션 전량 청산됐습니다.",
            )
        except Exception:
            pass

    elif kospi_chg <= -1.0:
        logger.warning("Dual signal %s+KOSPI%.1f%% → tighten stop losses", severity, kospi_chg)
        tightened = executor.tighten_stop_losses(reason=f"[{severity}] {reason}")
        try:
            notify_admins_error(
                "⚠️ 손절선 강화",
                f"{reason}\n\nKOSPI {kospi_chg:+.1f}% — 수익 포지션 {tightened}개 손절선을 현재가 기준으로 강화했습니다.",
            )
        except Exception:
            pass
    else:
        logger.info("Dual signal: AI=%s but KOSPI%.1f%% — no action taken", severity, kospi_chg)


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


def verify_run_market_outcomes() -> None:
    """
    전날 recommendation_runs의 실제 1일 KOSPI/KOSDAQ 변화율을 채운다.
    Stage1 market_theme 정확도 검증 데이터 축적용.
    스케줄러 16:00 잡에서 verify_news_events와 함께 호출.
    """
    from datetime import timedelta
    from sqlalchemy import select
    from app.core.database import SessionLocal
    from app.models.recommendation import RecommendationRun
    from app.services.kis.client import get_kis_client

    with SessionLocal() as db:
        now = datetime.now(timezone.utc)
        try:
            client = get_kis_client(db)
            kospi_now  = client._get_index_level("0001")
            kosdaq_now = client._get_index_level("1001")
        except Exception as e:
            logger.warning("Index fetch failed for run verification: %s", e)
            return

        if not kospi_now or not kosdaq_now:
            return

        # 1일 이상 경과했고 아직 검증 안 된 runs
        runs = db.scalars(
            select(RecommendationRun).where(
                RecommendationRun.verified_1d_at == None,
                RecommendationRun.kospi_at_run.isnot(None),
                RecommendationRun.run_date <= (now - timedelta(days=1)).date(),
            )
        ).all()

        for run in runs:
            if run.kospi_at_run and run.kospi_at_run > 0:
                run.kospi_change_1d  = ((kospi_now  - run.kospi_at_run)  / run.kospi_at_run  * 100).quantize(Decimal("0.0001"))
            if run.kosdaq_at_run and run.kosdaq_at_run > 0:
                run.kosdaq_change_1d = ((kosdaq_now - run.kosdaq_at_run) / run.kosdaq_at_run * 100).quantize(Decimal("0.0001"))
            run.verified_1d_at = now

        db.commit()
        logger.info("Run market outcome verification done: %d runs updated", len(runs))


# ------------------------------------------------------------------ #
# 보유 포지션 thesis 재검증
# ------------------------------------------------------------------ #

_THESIS_CHECK_PROMPT = """
당신은 한국 주식 포지션 리스크 감시 전문가입니다.
아래 보유 종목들에 대해 최근 2~3일 내 주가에 부정적 영향을 줄 수 있는 이슈를 검색하세요.

=== 보유 종목 및 매수 근거 ===
{positions_text}

각 종목별로 판단하세요:
- valid: 매수 근거 유효, 중요 부정적 이슈 없음
- partial: 일부 우려 있으나 thesis 근본은 유지됨
- invalid: 매수 근거가 뒤집혔거나 중대한 악재 발생 (공시, 규제, 핵심 사업 훼손 등)

【주의】 일반적인 시장 등락이나 섹터 전반의 분위기는 판단에서 제외.
해당 종목에 직접적으로 영향을 주는 뉴스/이슈만 감지하세요.

다음 JSON만 반환하세요:
{{
  "checks": [
    {{
      "stock_code": "종목코드",
      "thesis_valid": "valid" | "partial" | "invalid",
      "issues": "발견된 이슈 요약 (없으면 빈 문자열)",
      "confidence": 0.0~1.0
    }}
  ]
}}
"""

_THESIS_GROUP_SIZE = 8       # 그룹당 종목 수 (환각 방지)
_THESIS_CONFIDENCE_MIN = 0.7  # 이 이상일 때만 자동 조치


def check_position_theses() -> None:
    """
    보유 포지션의 매수 thesis를 현재 뉴스로 재검증.
    - 대상: 2일 이상 보유 OR 손실 중인 HOLDING 포지션
    - 8개씩 그룹 분할하여 그라운딩 검색 (환각 방지)
    - invalid + confidence>=0.7 + 손실 → 조기 청산
    - invalid + confidence>=0.7 + 수익 → 손절선 강화
    - partial → 텔레그램 알림만
    스케줄러에서 하루 2회(10:00, 14:00) 호출.
    """
    from datetime import date, timedelta
    from google import genai
    from google.genai import types
    from sqlalchemy import select
    from app.core.config import get_settings
    from app.core.database import SessionLocal
    from app.models.position import Position, PositionStatus
    from app.services.kis.client import get_kis_client_from_account

    api_key = get_settings().gemini_api_key
    if not api_key:
        return

    with SessionLocal() as db:
        today = date.today()
        two_days_ago = today - timedelta(days=2)

        positions = db.scalars(
            select(Position).where(Position.status == PositionStatus.HOLDING)
        ).all()

        # 대상 필터: 2일+ 보유 (신규 매수 제외)
        targets = [p for p in positions if p.entry_date <= two_days_ago]
        if not targets:
            logger.info("Thesis check: no positions to check")
            return

        logger.info("Thesis check: %d positions to check", len(targets))

        client_g = genai.Client(api_key=api_key)
        config = types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())]
        )

        # 8개씩 그룹 분할
        groups = [targets[i:i+_THESIS_GROUP_SIZE] for i in range(0, len(targets), _THESIS_GROUP_SIZE)]
        results: dict[str, dict] = {}

        for group in groups:
            lines = []
            for pos in group:
                days_held = (today - pos.entry_date).days
                ai_reason = ""
                if pos.recommendation:
                    ai_reason = pos.recommendation.ai_reason or ""
                lines.append(
                    f"[{pos.stock_code}] {days_held}일 보유\n"
                    f"매수근거: {ai_reason[:150] or '(수동매수)'}"
                )
            positions_text = "\n\n".join(lines)

            try:
                response = client_g.models.generate_content(
                    model=NEWS_MODEL,
                    contents=_THESIS_CHECK_PROMPT.format(positions_text=positions_text),
                    config=config,
                )
                text = response.text.strip()
                if "```" in text:
                    text = text.split("```")[1]
                    if text.startswith("json"):
                        text = text[4:]
                data = json.loads(text)
                for check in data.get("checks", []):
                    results[check["stock_code"]] = check
            except Exception as e:
                logger.error("Thesis check group failed: %s", e)

        if not results:
            return

        # 결과 처리
        from app.services.trading.executor import TradeExecutor
        from app.services.telegram.notifier import notify_admins_error

        executor = TradeExecutor(db)
        alerts = []

        for pos in targets:
            check = results.get(pos.stock_code)
            if not check:
                continue

            verdict    = check.get("thesis_valid", "valid")
            issues     = check.get("issues", "")
            confidence = float(check.get("confidence", 0.0))

            if verdict == "valid":
                continue

            try:
                client_k = get_kis_client_from_account(pos.account)
                current_price = client_k.get_current_price(pos.stock_code)
            except Exception as e:
                logger.error("Thesis check: price fetch failed for %s: %s", pos.stock_code, e)
                alerts.append(f"[{pos.stock_code}] {verdict} (가격조회 실패) — {issues}")
                continue

            in_loss = current_price < pos.entry_price
            stock_name = pos.recommendation.stock_name if pos.recommendation else pos.stock_code

            if verdict == "invalid" and confidence >= _THESIS_CONFIDENCE_MIN:
                if in_loss:
                    logger.warning("Thesis invalid → early close: %s", pos.stock_code)
                    executor._close_position(pos, current_price, PositionStatus.MANUAL_EXIT, client_k)
                    db.commit()
                    alerts.append(f"[{stock_name}] thesis 무효화 → 조기 청산 (손실 {float((current_price-pos.entry_price)/pos.entry_price*100):+.1f}%)\n사유: {issues}")
                else:
                    logger.warning("Thesis invalid → tighten stop: %s", pos.stock_code)
                    from app.services.trading.realtime_monitor import get_monitor
                    pos.peak_price     = current_price
                    pos.target_hit_at  = pos.target_hit_at or datetime.now(timezone.utc)
                    pos.target_hit_peak = current_price
                    pos.trailing_stop_override = True
                    get_monitor().force_trailing(str(pos.position_id), current_price)
                    db.commit()
                    stop = float(current_price * (1 - pos.strategy.stop_loss_pct / 100))
                    alerts.append(f"[{stock_name}] thesis 무효화 → 손절선 강화 (현재가 기준 {stop:,.0f}원)\n사유: {issues}")
            else:
                # partial or invalid with low confidence → alert only
                alerts.append(f"[{stock_name}] thesis {verdict} (확신도 {confidence:.0%}) — {issues}")

        if alerts:
            try:
                notify_admins_error(
                    "🔍 Thesis 재검증 결과",
                    "\n\n".join(alerts),
                )
            except Exception as e:
                logger.error("Thesis check telegram failed: %s", e)

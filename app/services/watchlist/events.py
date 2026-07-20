"""관심종목 이벤트 자동 감지 + 이벤트 트리거 자동 분석.

스펙(watchlist_spec.md) 원칙 2의 "이벤트 기반 트리거" 구현 — 감지는 전부
결정론(공식 API/적재 데이터, Gemini 0회), 분석·해석만 이벤트 발생 시 Gemini.

감지 유형:
- disclosure: DART 당일 신규 공시 (중요 유형 키워드 필터, rcept_no 중복 방지)
- flow_spike: 당일 외인/기관 순매수액이 30일 평균 절대값의 3배 이상 (적재 데이터)
- price_spike: 당일 등락률 ±5% 이상
- 실적 캘린더: 정기보고서 법정 제출기한 D-14/D-7 사전 안내
  (한국은 실적 발표일 사전 확정 공표가 드묾 — 법정 기한만 결정론으로 계산 가능.
   잠정실적 조기 공시는 disclosure 감지가 잡는다)

이벤트 감지 시: 종목 구독 유저 전원에게 알림 + (분석 트리거급이면) 유저별
run_analysis 자동 실행(일 1회 상한) → 무효화_조건 즉시 재판정 결과 동봉.
자동 청산·매매 없음 — 정보 push까지만 (판단은 사람).
"""
import json
import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.core.config_store import get_config, set_config

logger = logging.getLogger(__name__)

# 중요 공시 키워드 → 카테고리. 미매칭 공시는 알림 안 함 (잡공시 노이즈 방어)
_DISCLOSURE_CATEGORIES: list[tuple[str, list[str]]] = [
    # "영업(잠정)실적(공정공시)" 실제 표기가 괄호 포함이라 "(잠정)실적"으로 매칭
    ("실적",       ["잠정실적", "(잠정)실적", "영업실적", "분기보고서", "반기보고서",
                    "사업보고서", "실적공시", "손익구조"]),
    ("자본변동",   ["유상증자", "무상증자", "전환사채", "신주인수권", "교환사채", "감자"]),
    ("구조개편",   ["합병", "분할", "영업양수", "영업양도", "포괄적 주식교환"]),
    ("주요사항",   ["주요사항보고서"]),  # 자본시장법 주요 이벤트 포괄 (개별 키워드 미매칭 대비)
    ("대형계약",   ["단일판매", "공급계약", "장래사업", "경영계획"]),
    ("자사주",     ["자기주식"]),
    ("지배구조",   ["최대주주", "주식등의 대량보유"]),
    ("리스크",     ["소송", "거래정지", "관리종목", "불성실공시", "회생절차", "파산", "감사의견"]),
    ("조회공시",   ["조회공시", "풍문"]),
]
# 자동 분석까지 트리거하는 카테고리 (알림만: 자사주/지배구조/조회공시)
_ANALYZE_CATEGORIES = {"실적", "자본변동", "구조개편", "주요사항", "대형계약", "리스크"}

_FLOW_SPIKE_MULT = 3.0          # 30일 평균 |순매수| 대비 배수
_FLOW_SPIKE_MIN_EOK = 10.0      # 최소 절대액 (억원) — 소액 종목 노이즈 방어
_FLOW_MIN_COVERAGE = 20         # 배수 판정에 필요한 최소 적재 일수
_PRICE_SPIKE_PCT = 5.0

_SEEN_DISCLOSURES_KEY = "watchlist_seen_disclosures"   # {rcept_no: rcept_dt}
_EARNINGS_NOTICE_KEY = "watchlist_earnings_notice"     # {deadline_iso: [14, 7]}
_LAST_SCAN_KEY = "watchlist_events_last_scan"          # KST 날짜


def _kst_today() -> date:
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _load_json_config(db, key: str) -> dict:
    raw = get_config(db, key)
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        logger.warning("config %s JSON 손상 — 초기화", key)
        return {}


# ------------------------------------------------------------------ #
# 감지기 (결정론)
# ------------------------------------------------------------------ #

def _classify_disclosure(title: str) -> str | None:
    for category, keywords in _DISCLOSURE_CATEGORIES:
        if any(k in title for k in keywords):
            return category
    return None


def detect_disclosures(db, stock_code: str, today: date) -> list[dict]:
    """당일(주말 경과분 포함 최근 3일) 신규 중요 공시. rcept_no로 중복 억제."""
    from app.services.dart.client import fetch_recent_disclosures

    result = fetch_recent_disclosures(stock_code, end_date=today, days=3)
    if not result.get("available"):
        # 어댑터 실패는 이벤트 없음과 다름 — 로그만 남기고 스킵 (분석 어댑터와 동일 폴백 철학)
        logger.warning("disclosure scan unavailable for %s: %s", stock_code, result.get("note"))
        return []

    seen = _load_json_config(db, _SEEN_DISCLOSURES_KEY)
    events = []
    for it in result.get("items", []):
        rcept_no = it.get("rcept_no")
        if not rcept_no or rcept_no in seen:
            continue
        seen[rcept_no] = it.get("date") or today.strftime("%Y%m%d")
        category = _classify_disclosure(it.get("title", ""))
        if category is None:
            continue  # seen에는 기록 (재분류 목적 재알림 없음), 알림은 중요 유형만
        events.append({
            "kind": "disclosure",
            "category": category,
            "analyze": category in _ANALYZE_CATEGORIES,
            "trigger_type": "earnings" if category == "실적" else "disclosure",
            "line": f"공시[{category}] {it.get('title')} ({it.get('date')})",
            "url": it.get("url"),
        })

    # 30일 경과분 정리 후 저장
    cutoff = (today - timedelta(days=30)).strftime("%Y%m%d")
    seen = {k: v for k, v in seen.items() if (v or "") >= cutoff}
    set_config(db, _SEEN_DISCLOSURES_KEY, json.dumps(seen))
    return events


def detect_flow_spike(db, stock_code: str, today: date) -> list[dict]:
    """당일 외인/기관 순매수액이 직전 30거래일 평균 |순매수|의 3배 이상이면 이벤트."""
    from app.models.investor_flow import InvestorFlowDaily

    rows = db.execute(
        select(InvestorFlowDaily)
        .where(InvestorFlowDaily.stock_code == stock_code)
        .order_by(InvestorFlowDaily.trade_date.desc())
        .limit(31)
    ).scalars().all()
    if not rows or rows[0].trade_date != today:
        return []  # 당일 적재분 없음 (휴장/적재 실패) — 판정 불가는 침묵
    latest, history = rows[0], rows[1:]
    if len(history) < _FLOW_MIN_COVERAGE:
        return []

    events = []
    for attr, label in [("frgn_ntby_amt", "외국인"), ("orgn_ntby_amt", "기관")]:
        today_v = getattr(latest, attr)
        if today_v is None:
            continue
        hist_vals = [abs(float(getattr(r, attr))) for r in history
                     if getattr(r, attr) is not None]
        if len(hist_vals) < _FLOW_MIN_COVERAGE:
            continue
        avg30 = sum(hist_vals) / len(hist_vals)
        today_abs = abs(float(today_v))
        today_eok = today_abs / 100  # 백만원 → 억원
        if avg30 <= 0 or today_eok < _FLOW_SPIKE_MIN_EOK:
            continue
        if today_abs >= avg30 * _FLOW_SPIKE_MULT:
            side = "순매수" if float(today_v) > 0 else "순매도"
            events.append({
                "kind": "flow_spike",
                "analyze": True,
                "trigger_type": "flow_spike",
                "line": (f"수급 급변: {label} {side} {today_eok:,.0f}억원 "
                         f"(30일 평균의 {today_abs / avg30:.1f}배)"),
            })
    return events


def detect_price_spike(client, stock_code: str) -> list[dict]:
    """당일 등락률 ±5% 이상."""
    try:
        info = client.get_price_with_change(stock_code)
    except Exception as e:
        logger.warning("price scan failed for %s: %s", stock_code, e)
        return []
    chg = info.get("change_pct")
    if chg is None or abs(chg) < _PRICE_SPIKE_PCT:
        return []
    return [{
        "kind": "price_spike",
        "analyze": True,
        "trigger_type": "price_spike",
        "line": f"주가 급변: 당일 {chg:+.1f}% (종가 {info.get('price'):,}원)",
    }]


# ------------------------------------------------------------------ #
# 실적 캘린더 — 정기보고서 법정 제출기한 D-day 안내
# ------------------------------------------------------------------ #

def _report_deadlines(year: int) -> list[tuple[date, str]]:
    """12월 결산 기준 정기보고서 법정 제출기한 (자본시장법: 분기·반기 45일, 사업보고서 90일)."""
    return [
        (date(year, 3, 31) + timedelta(days=45), f"{year}년 1분기보고서"),
        (date(year, 6, 30) + timedelta(days=45), f"{year}년 반기보고서"),
        (date(year, 9, 30) + timedelta(days=45), f"{year}년 3분기보고서"),
        (date(year, 12, 31) + timedelta(days=90), f"{year}년 사업보고서"),
    ]


def earnings_calendar_notice(db, today: date) -> str | None:
    """다가오는 제출기한이 D-14/D-7 구간에 들어오면 1회 안내 문구 반환 (주말 밀림 허용)."""
    upcoming = [(d, label) for y in (today.year - 1, today.year)
                for d, label in _report_deadlines(y) if d >= today]
    if not upcoming:
        return None
    deadline, label = min(upcoming)
    days_until = (deadline - today).days
    stage = 14 if 8 <= days_until <= 14 else (7 if 0 <= days_until <= 7 else None)
    if stage is None:
        return None

    notified = _load_json_config(db, _EARNINGS_NOTICE_KEY)
    key = deadline.isoformat()
    if stage in notified.get(key, []):
        return None
    notified = {k: v for k, v in notified.items() if k >= today.isoformat()}  # 지난 기한 정리
    notified.setdefault(key, []).append(stage)
    set_config(db, _EARNINGS_NOTICE_KEY, json.dumps(notified))
    return (f"{label} 법정 제출기한 {deadline.isoformat()} (D-{days_until})\n"
            f"실제 발표는 기한 이전일 수 있고, 잠정실적은 별도 조기 공시될 수 있습니다 "
            f"(조기 공시는 공시 감지가 알립니다).")


# ------------------------------------------------------------------ #
# Phase 2 — 이벤트 트리거 자동 분석
# ------------------------------------------------------------------ #

def _auto_analyze(db, watch, trigger_type: str, today: date):
    """이벤트 발생 종목 자동 분석 (유저·종목·일 1회 상한). 반환: (StockAnalysis|None, note)."""
    from app.models.watchlist import StockAnalysis
    from app.services.watchlist.analyzer import run_analysis

    existing = db.scalar(
        select(StockAnalysis).where(
            StockAnalysis.user_id == watch.user_id,
            StockAnalysis.stock_code == watch.stock_code,
            StockAnalysis.analysis_date == today,
        ).limit(1)
    )
    if existing:
        return existing, "오늘자 분석이 이미 있어 재사용"
    try:
        analysis = run_analysis(db, watch.user_id, watch.stock_code, watch.stock_name,
                                watch.sector, today, trigger_type=trigger_type)
        return analysis, f"자동 분석 완료 (trigger: {trigger_type})"
    except Exception as e:
        logger.error("auto analysis failed for %s: %s", watch.stock_code, e)
        return None, f"자동 분석 실패 ({e.__class__.__name__}) — 앱에서 수동 실행해주세요"


def _condition_summary(db, client, analysis, fx_close) -> str:
    """방금 분석(또는 기존 오늘자 분석)의 무효화_조건 즉시 판정 요약."""
    from app.services.watchlist.invalidation import check_analysis

    try:
        check_analysis(db, client, analysis, fx_close)
        db.commit()
    except Exception as e:
        logger.error("event-time invalidation check failed for %s: %s",
                     analysis.stock_code, e)
        return "무효화_조건 판정 실패 — 16:20 정기 체크에서 재시도됩니다"

    items = (analysis.condition_status or {}).get("items", [])
    if not items:
        return "무효화_조건 없음"
    by_state: dict[str, int] = {}
    for it in items:
        by_state[it.get("state", "?")] = by_state.get(it.get("state", "?"), 0) + 1
    parts = []
    label = {"ok": "정상", "triggered": "⚠️충족", "pending_data": "데이터 대기",
             "manual": "수동 확인", "error": "오류"}
    for state, cnt in by_state.items():
        parts.append(f"{label.get(state, state)} {cnt}건")
    summary = " / ".join(parts)
    triggered_details = [f"「{c}」" for it, c in _triggered_conditions(analysis, items)]
    if triggered_details:
        summary += "\n  충족: " + ", ".join(triggered_details)
    return summary


def _triggered_conditions(analysis, items):
    conditions = (analysis.result or {}).get("무효화_조건") or []
    for i, it in enumerate(items):
        if it.get("state") == "triggered" and i < len(conditions):
            yield it, conditions[i].get("조건", "")


def _analysis_highlights(analysis) -> list[str]:
    """알림에 실을 분석 핵심 필드 — 해석은 참고 정보, 판단 지시 아님."""
    result = analysis.result or {}
    lines = []
    catalyst = result.get("단기_촉매")
    if isinstance(catalyst, list):
        catalyst = catalyst[0] if catalyst else None
    if catalyst:
        lines.append(f"단기 촉매: {str(catalyst)[:200]}")
    valuation = result.get("밸류_코멘트")
    if valuation:
        lines.append(f"밸류: {str(valuation)[:200]}")
    return lines


# ------------------------------------------------------------------ #
# 스캔 진입점 (16:30 평일 잡 + 수동 트리거)
# ------------------------------------------------------------------ #

def scan_watchlist_events(force: bool = False) -> dict:
    """관심종목 전체 이벤트 스캔 → 유저 알림 + 트리거급 이벤트는 자동 분석.

    반환: {"scanned": n, "events": n, "analyzed": n} (수동 트리거 응답용)
    """
    from app.core.database import SessionLocal
    from app.models.user import User
    from app.models.watchlist import WatchlistStock
    from app.services.kis.client import get_kis_client
    from app.services.telegram.notifier import get_notifier

    stats = {"scanned": 0, "events": 0, "analyzed": 0}
    with SessionLocal() as db:
        watches = db.scalars(select(WatchlistStock)).all()
        if not watches:
            return stats
        client = get_kis_client(db)
        today = _kst_today()

        # 휴장 확정 시만 스킵 — 판정 실패(None)는 개장 간주 (감시 공백 방지)
        if client.is_market_open_day(today.strftime("%Y%m%d")) is False:
            logger.info("Watchlist event scan: market closed — skip")
            return stats

        # 수급/주가 감지는 일 1회 (수동 재트리거 시 force로 우회, 공시는 rcept_no가 자체 중복 방지)
        already_scanned = get_config(db, _LAST_SCAN_KEY) == today.isoformat()
        set_config(db, _LAST_SCAN_KEY, today.isoformat())

        # 환율 — 조건 즉시 판정용 공통 팩터 1회 조회 (invalidation 잡과 동일 패턴)
        try:
            fx_rows = client.get_fx_daily_closes(days=5)
            fx_close = fx_rows[0]["close"] if fx_rows else None
        except Exception:
            fx_close = None

        # 종목 단위 감지 (공용) → 유저 단위 fan-out
        events_by_code: dict[str, list[dict]] = {}
        codes = {w.stock_code for w in watches}
        for code in sorted(codes):
            stats["scanned"] += 1
            events = detect_disclosures(db, code, today)
            if not already_scanned or force:
                events += detect_flow_spike(db, code, today)
                events += detect_price_spike(client, code)
            if events:
                events_by_code[code] = events
                stats["events"] += len(events)

        calendar_note = earnings_calendar_notice(db, today)

        notifier = get_notifier()

        for w in watches:
            events = events_by_code.get(w.stock_code)
            if not events:
                continue
            analyze_triggers = [e for e in events if e.get("analyze")]
            analysis, analysis_note, cond_summary = None, None, None
            if analyze_triggers:
                # 첫 트리거의 유형을 대표로 기록 (실적 > 기타 순으로 정렬돼 있진 않으므로 우선순위 부여)
                trigger = next((e for e in analyze_triggers if e["trigger_type"] == "earnings"),
                               analyze_triggers[0])
                analysis, analysis_note = _auto_analyze(db, w, trigger["trigger_type"], today)
                if analysis is not None:
                    stats["analyzed"] += 1
                    cond_summary = _condition_summary(db, client, analysis, fx_close)

            if notifier:
                chat_id = db.scalar(
                    select(User.telegram_chat_id).where(User.user_id == w.user_id))
                if chat_id:
                    try:
                        notifier.notify_watchlist_event(
                            chat_id, w.stock_name, w.stock_code,
                            [e["line"] + (f"\n  {e['url']}" if e.get("url") else "")
                             for e in events],
                            analysis_note=analysis_note,
                            highlights=_analysis_highlights(analysis) if analysis else [],
                            condition_summary=cond_summary,
                        )
                    except Exception as e:
                        logger.error("event notify failed for %s: %s", w.stock_code, e)

        # 실적 캘린더는 유저당 1회 (종목 무관 공통 일정)
        if calendar_note and notifier:
            for user_id in {w.user_id for w in watches}:
                chat_id = db.scalar(
                    select(User.telegram_chat_id).where(User.user_id == user_id))
                if chat_id:
                    try:
                        notifier.notify_warning(chat_id, "관심종목 실적 캘린더", calendar_note)
                    except Exception as e:
                        logger.error("calendar notify failed: %s", e)

        logger.info("Watchlist event scan: %d codes, %d events, %d auto-analyzed",
                    stats["scanned"], stats["events"], stats["analyzed"])
    return stats

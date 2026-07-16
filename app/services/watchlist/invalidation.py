"""관심종목 무효화_조건 자동 체크 (16:20 잡).

설계 원칙 (docs/watchlist_spec.md + CLAUDE.md 파생 원칙):
- 판정은 앱이 결정론적으로 수행 — LLM에 재계산/비교를 시키지 않는다.
- LLM은 분석 시점에 조건을 구조화(check_type + params)해 낼 뿐이며,
  구조가 불완전하면 자동 체크를 포기하고 manual로 강등한다 (오판보다 보류).
- 데이터 부족 구간은 부분 데이터로 판정하지 않고 pending_data로 보류 + 사유 명시
  (flow_store의 "부분합 위장 금지"와 동일 철학).
- 알림은 상태 전이(미충족→충족) 시에만 — 경계 근처 왕복으로 인한 노이즈 방지.
- 자동 청산 없음: 이 탭은 수동매매 일지 — 감시는 기계, 행동 판단은 사람.
"""
import logging
from datetime import datetime, timezone

from sqlalchemy import select

logger = logging.getLogger(__name__)

AUTO_TYPES = {"flow", "fx", "valuation", "earnings", "consensus"}
ALL_TYPES = AUTO_TYPES | {"manual"}

_EARNINGS_METRICS = {"op_margin_q_pct", "op_yoy_pct", "revenue_yoy_pct", "ni_yoy_pct"}
_CONSENSUS_METRICS = {"operating_profit", "eps", "revenue"}

# 조건 상태:
#   ok           — 체크됨, 미충족 (논거 유효)
#   triggered    — 충족 (논거 훼손 신호) → 전이 시 텔레그램
#   pending_data — 판정에 필요한 데이터 미도래/커버리지 부족 (사유 명시)
#   manual       — 자동 감시 불가, 사람이 확인
#   error        — 체크 중 오류 (판정 아님)


# ------------------------------------------------------------------ #
# 조건 정규화 — LLM 출력 검증, 불완전하면 manual 강등
# ------------------------------------------------------------------ #

def _f(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _validate_params(check_type: str, params: dict) -> dict | None:
    """타입별 파라미터 검증/정제. 실패 시 None (호출부에서 manual 강등)."""
    if check_type == "flow":
        investor = params.get("investor")
        metric = params.get("metric")
        direction = params.get("direction", "sell")
        days = _f(params.get("days"))
        if investor not in ("frgn", "orgn") or direction not in ("sell", "buy"):
            return None
        if metric not in ("consecutive_days", "cum_amount") or not days or not (1 <= days <= 120):
            return None
        out = {"investor": investor, "direction": direction,
               "metric": metric, "days": int(days)}
        if metric == "cum_amount":
            amount = _f(params.get("amount_eok"))
            if not amount or amount <= 0:
                return None
            out["amount_eok"] = amount
        return out

    if check_type == "fx":
        op, level = params.get("op"), _f(params.get("level"))
        if op not in ("above", "below") or not level or not (500 <= level <= 3000):
            return None
        return {"op": op, "level": level}

    if check_type == "valuation":
        op, value = params.get("op"), _f(params.get("value"))
        if params.get("metric") != "pbr_percentile_5y":
            return None
        if op not in ("above", "below") or value is None or not (0 <= value <= 100):
            return None
        return {"metric": "pbr_percentile_5y", "op": op, "value": value}

    if check_type == "earnings":
        period = str(params.get("period") or "")
        metric, op, value = params.get("metric"), params.get("op"), _f(params.get("value"))
        if len(period) != 6 or not period.isdigit() or period[4:] not in ("03", "06", "09", "12"):
            return None
        if metric not in _EARNINGS_METRICS or op not in ("above", "below") or value is None:
            return None
        return {"period": period, "metric": metric, "op": op, "value": value}

    if check_type == "consensus":
        year = str(params.get("year") or "")
        metric, drop = params.get("metric"), _f(params.get("drop_pct"))
        if len(year) != 4 or not year.isdigit():
            return None
        if metric not in _CONSENSUS_METRICS or not drop or drop <= 0:
            return None
        return {"year": year, "metric": metric, "drop_pct": drop}

    return None


def normalize_conditions(raw) -> list[dict]:
    """LLM의 무효화_조건 출력 → 검증된 구조 배열.

    - 문자열(구 포맷) → manual 강등
    - dict인데 params 불완전 → manual 강등 + spec_note (오판 방지가 자동화보다 우선)
    """
    out = []
    for item in raw or []:
        if isinstance(item, str):
            out.append({"조건": item, "check_type": "manual",
                        "params": {"확인_방법": "상시 뉴스/공시 확인"}})
            continue
        if not isinstance(item, dict):
            continue
        text = str(item.get("조건") or item.get("condition") or item.get("text") or "").strip()
        if not text:
            continue
        check_type = item.get("check_type")
        params = item.get("params") if isinstance(item.get("params"), dict) else {}
        if check_type == "manual":
            method = str(params.get("확인_방법") or params.get("how") or "").strip()
            out.append({"조건": text, "check_type": "manual",
                        "params": {"확인_방법": method or "상시 뉴스/공시 확인"}})
            continue
        valid = _validate_params(check_type, params) if check_type in AUTO_TYPES else None
        if valid is None:
            out.append({"조건": text, "check_type": "manual",
                        "params": {"확인_방법": "상시 뉴스/공시 확인"},
                        "spec_note": f"자동 감시 스펙 불충분 (원 유형: {check_type}) — 수동 확인으로 강등"})
        else:
            out.append({"조건": text, "check_type": check_type, "params": valid})
    return out


# ------------------------------------------------------------------ #
# 타입별 결정론 체크 — 반환 (state, detail)
# ------------------------------------------------------------------ #

def _fmt_eok(v_million: float) -> str:
    eok = v_million / 100
    if abs(eok) >= 10000:
        return f"{eok / 10000:+,.2f}조"
    return f"{eok:+,.0f}억"


def _check_flow(db, stock_code: str, p: dict) -> tuple[str, str]:
    from app.models.investor_flow import InvestorFlowDaily
    rows = db.execute(
        select(InvestorFlowDaily)
        .where(InvestorFlowDaily.stock_code == stock_code)
        .order_by(InvestorFlowDaily.trade_date.desc())
        .limit(130)
    ).scalars().all()
    if not rows:
        return "pending_data", "수급 적재 이력 없음 — 16:10 잡 축적 후 판정 가능"

    attr = f"{p['investor']}_ntby_amt"
    sign = -1 if p["direction"] == "sell" else 1
    label = ("외국인" if p["investor"] == "frgn" else "기관") + \
            (" 순매도" if p["direction"] == "sell" else " 순매수")
    days = p["days"]

    if p["metric"] == "consecutive_days":
        streak = 0
        for r in rows:
            v = getattr(r, attr)
            if v is not None and sign * float(v) > 0:
                streak += 1
            else:
                break
        if streak >= days:
            return "triggered", f"{label} 연속 {streak}거래일 (기준 {days}거래일)"
        if streak == len(rows) and len(rows) < days:
            # 적재분 전체가 한 방향 — 그 이전이 확인 불가라 판정 보류 (부분 데이터로 단정 금지)
            return "pending_data", f"적재 {len(rows)}거래일 전부 {label} — {days}거래일 판정엔 커버리지 부족"
        return "ok", f"현재 {label} 연속 {streak}거래일 — 기준 {days}거래일 미달"

    # cum_amount
    if len(rows) < days:
        return "pending_data", f"적재 {len(rows)}거래일 — {days}거래일 누적 판정은 커버리지 도달 후"
    vals = [float(getattr(r, attr)) for r in rows[:days] if getattr(r, attr) is not None]
    if not vals:
        return "pending_data", "수급 금액 데이터 결측"
    cum = sum(vals)
    threshold = p["amount_eok"] * 100  # 억원 → 백만원
    hit = cum <= -threshold if p["direction"] == "sell" else cum >= threshold
    state = "triggered" if hit else "ok"
    return state, f"{days}거래일 누적 {_fmt_eok(cum)} (기준 {'-' if p['direction'] == 'sell' else '+'}{p['amount_eok']:,.0f}억)"


def _check_fx(fx_close: float | None, p: dict) -> tuple[str, str]:
    if fx_close is None:
        return "error", "환율 조회 실패 — 판정 보류"
    hit = fx_close >= p["level"] if p["op"] == "above" else fx_close <= p["level"]
    word = "상회" if p["op"] == "above" else "하회"
    return ("triggered" if hit else "ok"), \
        f"USD/KRW 현재 {fx_close:,.1f} — 기준 {p['level']:,.0f} {word} {'충족' if hit else '미충족'}"


def _check_valuation(client, stock_code: str, p: dict) -> tuple[str, str]:
    from app.services.watchlist.analyzer import _pbr_band_5y
    holding = client.get_foreign_holding(stock_code)
    band = _pbr_band_5y(client, stock_code, holding.get("pbr") if holding else None)
    if not band.get("available"):
        return "pending_data", "PBR 5년 밴드 계산 불가 (BPS/월봉 데이터 부족)"
    pct = band["pbr_percentile_5y"]
    hit = pct >= p["value"] if p["op"] == "above" else pct <= p["value"]
    word = "이상" if p["op"] == "above" else "이하"
    return ("triggered" if hit else "ok"), \
        f"PBR 5년 퍼센타일 현재 {pct:.1f}% — 기준 {p['value']:.0f}% {word} {'충족' if hit else '미충족'}"


def _check_earnings(client, stock_code: str, p: dict) -> tuple[str, str]:
    from app.services.watchlist.analyzer import _derive_quarters
    income = client.get_income_statements(stock_code, quarterly=True)
    quarters = _derive_quarters(income[:10])
    entry = next((q for q in quarters if q.get("period") == p["period"]), None)
    q_label = f"{p['period'][:4]}년 {int(p['period'][4:]) // 3}분기"
    if entry is None:
        return "pending_data", f"대상 분기({q_label}) 실적 미공시 — 공시 반영 후 자동 판정"
    val = entry.get(p["metric"])
    if val is None:
        return "pending_data", f"{q_label} {p['metric']} 값 결측 — 판정 불가"
    hit = val <= p["value"] if p["op"] == "below" else val >= p["value"]
    word = "이하" if p["op"] == "below" else "이상"
    return ("triggered" if hit else "ok"), \
        f"{q_label} {p['metric']} 실측 {val:+.2f} — 기준 {p['value']:.2f} {word} {'충족' if hit else '미충족'}"


def _check_consensus(client, stock_code: str, snapshot: dict | None, p: dict) -> tuple[str, str]:
    baseline = (snapshot or {}).get("consensus_estimate")
    if not baseline or not baseline.get("periods"):
        return "pending_data", "분석 시점 컨센서스 기준값 없음 — 하향폭 판정 불가"
    current = client.get_estimate_perform(stock_code)
    if not current or not current.get("periods"):
        return "pending_data", "현재 컨센서스 조회 불가 (커버리지 없음)"

    def _pick(est: dict) -> float | None:
        vals = est.get(p["metric"]) or []
        for period, v in zip(est.get("periods") or [], vals):
            if p["year"] in str(period) and "E" in str(period).upper():
                return _f(v)
        return None

    base_v, cur_v = _pick(baseline), _pick(current)
    if base_v is None or base_v <= 0:
        return "pending_data", f"{p['year']}E {p['metric']} 분석 시점 추정치 없음/비양수 — 판정 불가"
    if cur_v is None:
        return "pending_data", f"{p['year']}E {p['metric']} 현재 추정치 조회 불가"
    change = (cur_v / base_v - 1) * 100
    hit = change <= -p["drop_pct"]
    return ("triggered" if hit else "ok"), \
        f"{p['year']}E {p['metric']} 컨센서스 분석시점 대비 {change:+.1f}% (기준 -{p['drop_pct']:.0f}%)"


def evaluate_condition(db, client, stock_code: str, snapshot: dict | None,
                       cond: dict, fx_close: float | None) -> tuple[str, str]:
    """조건 1건 판정. 예외는 error로 — 오류를 ok/triggered로 위장하지 않는다."""
    ct = cond.get("check_type")
    if ct == "manual":
        method = (cond.get("params") or {}).get("확인_방법", "상시 뉴스/공시 확인")
        return "manual", f"자동 감시 불가 — 확인: {method}"
    try:
        p = cond.get("params") or {}
        if ct == "flow":
            return _check_flow(db, stock_code, p)
        if ct == "fx":
            return _check_fx(fx_close, p)
        if ct == "valuation":
            return _check_valuation(client, stock_code, p)
        if ct == "earnings":
            return _check_earnings(client, stock_code, p)
        if ct == "consensus":
            return _check_consensus(client, stock_code, snapshot, p)
        return "manual", f"알 수 없는 유형({ct}) — 수동 확인"
    except Exception as e:
        logger.warning("condition check failed (%s, %s): %s", stock_code, ct, e)
        return "error", f"체크 오류 — {e}"


# ------------------------------------------------------------------ #
# 분석 단위 체크 + 상태 전이 감지
# ------------------------------------------------------------------ #

def check_analysis(db, client, analysis, fx_close: float | None) -> list[dict]:
    """최신 분석 1건의 조건 전체 판정 → condition_status 갱신.

    반환: 이번 체크에서 새로 충족(triggered 전이)된 조건 목록 (알림용).
    """
    conditions = normalize_conditions((analysis.result or {}).get("무효화_조건"))
    if not conditions:
        return []

    prev_items = (analysis.condition_status or {}).get("items", [])
    now_iso = datetime.now(timezone.utc).isoformat()
    items, newly_triggered = [], []

    for i, cond in enumerate(conditions):
        state, detail = evaluate_condition(
            db, client, analysis.stock_code, analysis.input_snapshot, cond, fx_close)
        prev = prev_items[i] if i < len(prev_items) else {}
        item = {
            "state": state,
            "detail": detail,
            "check_type": cond.get("check_type"),
            "triggered_at": prev.get("triggered_at"),
            "notified_at": prev.get("notified_at"),
        }
        if state == "triggered":
            if prev.get("state") != "triggered":
                item["triggered_at"] = now_iso
                item["notified_at"] = now_iso
                newly_triggered.append({"조건": cond["조건"], "detail": detail})
        else:
            # 충족 해제 → 이력 초기화 (재충족 시 다시 알림)
            item["triggered_at"] = None
            item["notified_at"] = None
        items.append(item)

    analysis.condition_status = {"checked_at": now_iso, "items": items}
    return newly_triggered


# ------------------------------------------------------------------ #
# 스케줄러 잡 진입점 (16:20 평일 — 16:10 수급 적재 직후)
# ------------------------------------------------------------------ #

def check_all_watchlist_invalidations() -> None:
    """전체 유저 관심종목의 최신 분석 1건씩 무효화_조건 판정 + 전이 시 유저 알림."""
    from app.core.database import SessionLocal
    from app.models.user import User
    from app.models.watchlist import WatchlistStock, StockAnalysis
    from app.services.kis.client import get_kis_client
    from app.services.telegram.notifier import get_notifier

    with SessionLocal() as db:
        watches = db.scalars(select(WatchlistStock)).all()
        if not watches:
            return
        client = get_kis_client(db)

        # 환율은 전 종목 공통 팩터 — 1회만 조회
        try:
            fx_rows = client.get_fx_daily_closes(days=5)
            fx_close = fx_rows[0]["close"] if fx_rows else None
        except Exception as e:
            logger.warning("fx fetch failed: %s", e)
            fx_close = None

        per_user_alerts: dict = {}  # user_id → [(stock_name, code, [items])]
        checked = 0
        for w in watches:
            analysis = db.scalar(
                select(StockAnalysis)
                .where(StockAnalysis.user_id == w.user_id,
                       StockAnalysis.stock_code == w.stock_code)
                .order_by(StockAnalysis.analysis_date.desc(),
                          StockAnalysis.created_at.desc())
                .limit(1)
            )
            if not analysis or not (analysis.result or {}).get("무효화_조건"):
                continue
            try:
                triggered = check_analysis(db, client, analysis, fx_close)
                checked += 1
            except Exception as e:
                logger.error("invalidation check failed for %s: %s", w.stock_code, e)
                continue
            if triggered:
                per_user_alerts.setdefault(w.user_id, []).append(
                    (w.stock_name, w.stock_code, triggered))
        db.commit()
        logger.info("Invalidation check: %d analyses checked, %d stocks newly triggered",
                    checked, sum(len(v) for v in per_user_alerts.values()))

        notifier = get_notifier()
        if not notifier or not per_user_alerts:
            return
        for user_id, alerts in per_user_alerts.items():
            chat_id = db.scalar(select(User.telegram_chat_id).where(User.user_id == user_id))
            if not chat_id:
                continue
            for stock_name, stock_code, items in alerts:
                notifier.notify_invalidation_triggered(chat_id, stock_name, stock_code, items)


# ------------------------------------------------------------------ #
# 분석 완료 시 1회 안내 — 수동 확인 필요 조건 + 자동 감시 대상 요약
# ------------------------------------------------------------------ #

def send_condition_notice(db, user_id, stock_name: str, stock_code: str,
                          conditions: list[dict]) -> None:
    """분석 직후: 자동 감시 대상/수동 확인 조건을 유저 텔레그램으로 안내 (best-effort)."""
    from app.models.user import User
    from app.services.telegram.notifier import get_notifier

    notifier = get_notifier()
    if not notifier or not conditions:
        return
    chat_id = db.scalar(select(User.telegram_chat_id).where(User.user_id == user_id))
    if not chat_id:
        return
    auto = [c for c in conditions if c.get("check_type") in AUTO_TYPES]
    manual = [c for c in conditions if c.get("check_type") == "manual"]
    notifier.notify_watchlist_conditions(chat_id, stock_name, stock_code, auto, manual)

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
            if amount and amount > 0:
                out["amount_eok"] = amount
            # 금액 미지정은 스펙 결함이 아니다 — 임계는 LLM이 아니라 앱이 분포에서
            # 정한다(2026-09-07 원칙). calibration이 채우고, 표본 부족으로 못 채우면
            # finalize_conditions()가 그때 manual로 강등한다.
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
# 조건 품질 스크리닝 — 생성 직후 1회 (결정론, 추가 API 0회)
# ------------------------------------------------------------------ #
#
# 배경(2026-08-28): 구조가 유효해도 감시 가치가 없는 조건이 섞인다.
#   ① 이미 충족/임박 — "조건"이 아니라 예정된 사건 (외인 누적 10조 기준인데 이미 6.7조)
#   ② YoY 성장률 임계 — 기저효과에 종속돼 논거와 무관하게 자동 충족/붕괴
#   ③ 밸류 하향 조건 — 싸지는 것은 강세 논거의 반증이 아님 (방향 역전)
#   ④ 최근 밴드 안의 환율 레벨 — 이례 신호가 아님
# 판정 재료가 전부 input_snapshot + 적재 수급이라 LLM에 되묻지 않고 앱이 거른다.
# 결함 조건은 삭제가 아니라 manual 강등 — 서술은 남기되 자동 감시에서만 뺀다.
#
# 양쪽 꼬리 보강 (2026-09-07): 위 ①~④는 전부 "너무 쉬운 조건"만 본다. 실측에서
# 드러난 반대편 결함 — 현재 수준에서 너무 멀어 **사실상 절대 발동하지 않는 조건**
# (환율 1560원 vs 현재 1346 / 영업이익률 50% vs 현재 76%) — 은 전부 통과했다.
# 항상 초록이거나 항상 노랑인 신호등은 신호등이 아니다. ⑤~⑧이 그 반대편을 막는다:
#   ⑤ 과거 창 발동률이 FIRE_RATE_MAX 초과 (평상시 수준) 또는 0 (도달 불가)
#   ⑥ 환율 임계가 지평 대비 _FX_SIGMA_MAX σ 밖
#   ⑦ 분기 마진 임계가 최근 마진 대비 _MARGIN_SIGMA_MAX σ 밖
#   ⑧ 컨센서스 하향폭이 실적 붕괴 수준 (_CONSENSUS_MAX_DROP 초과)

_PROXIMITY_RATIO = 0.7      # 임계의 70% 이미 도달 → 사실상 예정된 사건
_YOY_MAX_ABS = 100.0        # YoY 성장률 임계 상한 (%)
_CONSENSUS_MIN_DROP = 2.0   # 컨센서스 통상 변동폭 — 이 미만 하향은 노이즈
_CONSENSUS_MAX_DROP = 25.0  # 이 이상 하향은 실적 붕괴 — 논거 반증 신호로는 늦다
_YOY_METRICS = {"op_yoy_pct", "revenue_yoy_pct", "ni_yoy_pct"}


def _flow_state(db, stock_code: str, investor: str, direction: str,
                days: int) -> tuple[float | None, int, int]:
    """(기간 누적 백만원, 현재 연속일, 확정 적재 일수) — 스크리닝용 원시값.

    창은 확정 데이터만으로 채운다 — 미확정 당일 행(NULL)이 자리를 차지하면
    "5거래일 누적"이 4일치로 계산돼 게이트가 헐거워진다 (2026-09-07 교정).
    """
    from app.services.watchlist.calibration import flow_series

    vals = flow_series(db, stock_code, investor)
    if not vals:
        return None, 0, 0
    sign = -1 if direction == "sell" else 1
    streak = 0
    for v in vals:
        if sign * v > 0:
            streak += 1
        else:
            break
    cum = sum(vals[:days]) if len(vals) >= days else None
    return cum, streak, len(vals)


def _screen_reason(db, stock_code: str, snapshot: dict | None, cond: dict) -> str | None:
    """조건 품질 결함 사유 (없으면 None). 스냅샷/적재 수급만 사용 — API 호출 없음."""
    ct, p = cond.get("check_type"), cond.get("params") or {}
    snap = snapshot or {}

    if ct == "flow":
        from app.services.watchlist.calibration import (
            FIRE_RATE_MAX, MIN_WINDOWS, flow_series, fire_rate, rolling_sums, streak_rate)

        if p["metric"] == "cum_amount" and not p.get("amount_eok"):
            return None  # 임계 미확정 — finalize 단계에서 판정

        cum, streak, coverage = _flow_state(
            db, stock_code, p["investor"], p["direction"], p["days"])
        if p["metric"] == "cum_amount":
            if cum is not None:
                threshold = p["amount_eok"] * 100  # 억원 → 백만원
                sign = -1 if p["direction"] == "sell" else 1
                progress = (sign * cum) / threshold if threshold else 0
                if progress >= 1:
                    return f"분석 시점에 이미 충족 ({p['days']}거래일 누적 {_fmt_eok(cum)})"
                if progress >= _PROXIMITY_RATIO:
                    return (f"현재 누적이 임계의 {progress:.0%}에 도달 "
                            f"({_fmt_eok(cum)} / 기준 {p['amount_eok']:,.0f}억) — "
                            f"조건이 아니라 예정된 사건")
            # 발동률 — 현재값 근접만으로는 "평상시 수준"인 임계를 못 잡는다
            sums = rolling_sums(flow_series(db, stock_code, p["investor"]), p["days"])
            if len(sums) >= MIN_WINDOWS:
                rate = fire_rate(sums, p["amount_eok"] * 100, p["direction"])
                if rate > FIRE_RATE_MAX:
                    return (f"과거 {len(sums)}개 {p['days']}일 창 중 {rate:.0%}에서 충족 — "
                            f"평상시 수준이라 무효화 신호가 아님")
                if rate == 0:
                    return (f"과거 {len(sums)}개 {p['days']}일 창에서 한 번도 충족된 적 없음 — "
                            f"사실상 발동 불가")
        elif p["metric"] == "consecutive_days":
            if coverage:
                if streak >= p["days"]:
                    return f"분석 시점에 이미 충족 (현재 연속 {streak}거래일)"
                if streak >= p["days"] * _PROXIMITY_RATIO:
                    return (f"현재 연속 {streak}거래일 / 기준 {p['days']}거래일 — "
                            f"임계 임박, 감시 가치 낮음")
            vals = flow_series(db, stock_code, p["investor"])
            if len(vals) >= MIN_WINDOWS:
                rate = streak_rate(vals, p["days"], p["direction"])
                if rate > FIRE_RATE_MAX:
                    return (f"적재 {len(vals)}거래일 중 {rate:.0%} 구간에서 "
                            f"{p['days']}일 연속이 관측됨 — 평상시 수준")
                if rate == 0 and p["days"] > len(vals) / 4:
                    return (f"적재 {len(vals)}거래일에서 {p['days']}일 연속이 "
                            f"한 번도 관측된 적 없음 — 사실상 발동 불가")
        return None

    if ct == "fx":
        fx = snap.get("fx_usdkrw") or {}
        if not fx.get("available"):
            return None
        cur = fx.get("current")
        # 기준 진폭은 1개월 — 3개월 밴드는 추세 이동을 담아 "밴드 밖"이 곧 도달 불가가
        # 되는 구간이 있다 (2026-09-07 교정). 구 스냅샷은 3개월로 폴백.
        lo = fx.get("low_1m", fx.get("low_3m"))
        hi = fx.get("high_1m", fx.get("high_3m"))
        window = "1개월" if fx.get("high_1m") is not None else "3개월"
        if cur is not None:
            if (p["op"] == "above" and cur >= p["level"]) or \
               (p["op"] == "below" and cur <= p["level"]):
                return f"분석 시점에 이미 충족 (현재 {cur:,.1f})"
        if lo is not None and hi is not None and lo <= p["level"] <= hi:
            return (f"기준 {p['level']:,.0f}원이 최근 {window} 진폭({lo:,.0f}~{hi:,.0f}) 안 — "
                    f"이례 신호가 아님")
        # 반대편 꼬리 — 밴드 밖이기만 하면 되는 게 아니라 도달 가능해야 한다
        from app.services.watchlist.calibration import _FX_SIGMA_MAX, _fx_sigma_move
        sigma = _fx_sigma_move(fx)
        if sigma and cur is not None:
            dist = abs(p["level"] - cur) / sigma
            if dist > _FX_SIGMA_MAX:
                return (f"기준 {p['level']:,.0f}원은 현재 {cur:,.0f}원에서 {dist:.1f}σ "
                        f"(1개월 1σ ≈ {sigma:,.0f}원) — 사실상 발동 불가")
        return None

    if ct == "valuation":
        if p["op"] == "below":
            return ("밸류에이션 하락(저평가화)은 강세 논거의 반증이 아님 — 방향 역전. "
                    "밸류 조건은 고평가 진입(above) 방향으로 쓸 것")
        band = ((snap.get("valuation_current") or {}).get("pbr_band_5y") or {})
        pct = band.get("pbr_percentile_5y") if band.get("available") else None
        if pct is not None and pct >= p["value"]:
            return f"분석 시점에 이미 충족 (현재 퍼센타일 {pct:.1f}%)"
        if p["value"] >= 98:
            return (f"기준 퍼센타일 {p['value']:.0f}% — 5년 최고 밸류 구간이라 "
                    f"사실상 발동 불가")
        return None

    if ct == "earnings":
        if p["metric"] in _YOY_METRICS and abs(p["value"]) > _YOY_MAX_ABS:
            return (f"YoY 성장률 임계 {p['value']:+.0f}% — 기저효과 종속. "
                    f"전년 동기 수준에 따라 논거와 무관하게 충족/붕괴한다. "
                    f"마진(op_margin_q_pct) 등 절대 수준 지표를 쓸 것")
        quarters = ((snap.get("fundamentals_quarterly") or {}).get("income_single_q")) or []
        entry = next((q for q in quarters if q.get("period") == p["period"]), None)
        val = entry.get(p["metric"]) if entry else None
        if val is not None:
            hit = val <= p["value"] if p["op"] == "below" else val >= p["value"]
            if hit:
                return f"대상 분기가 이미 공시됐고 분석 시점에 이미 충족 (실측 {val:+.2f})"
        # 반대편 꼬리 — 최근 마진에서 너무 멀면 실적이 무너져도 안 켜진다
        if p["metric"] == "op_margin_q_pct":
            from app.services.watchlist.calibration import _MARGIN_SIGMA_MAX, margin_sigma
            ms = margin_sigma(snap)
            if ms:
                latest, sd = ms
                dist = abs(latest - p["value"]) / sd
                if dist > _MARGIN_SIGMA_MAX:
                    return (f"기준 {p['value']:.1f}%는 최근 분기 영업이익률 {latest:.1f}%에서 "
                            f"{dist:.1f}σ (분기 변화폭 σ {sd:.1f}%p) — 사실상 발동 불가")
        return None

    if ct == "consensus":
        if p["drop_pct"] < _CONSENSUS_MIN_DROP:
            return (f"하향 임계 {p['drop_pct']:.1f}% — 컨센서스 통상 변동폭 이내로 노이즈에 반응")
        if p["drop_pct"] > _CONSENSUS_MAX_DROP:
            return (f"하향 임계 {p['drop_pct']:.0f}% — 실적 붕괴 수준이라 "
                    f"논거 반증 신호로는 너무 늦다")
        return None

    return None


def screen_conditions(db, stock_code: str, snapshot: dict | None,
                      conditions: list[dict]) -> tuple[list[dict], list[tuple[dict, str]]]:
    """정규화된 조건을 품질 기준으로 분리. 반환: (통과, [(조건, 사유)])."""
    kept, rejected = [], []
    for cond in conditions:
        if cond.get("check_type") == "manual":
            kept.append(cond)
            continue
        try:
            reason = _screen_reason(db, stock_code, snapshot, cond)
        except Exception as e:  # 스크리닝 실패가 분석을 막지 않는다
            logger.warning("condition screening failed (%s): %s", stock_code, e)
            reason = None
        (rejected.append((cond, reason)) if reason else kept.append(cond))
    return kept, rejected


# 조건 중복/모순 판정 키 — 이 함수가 유일한 기준이다 (analyzer도 이걸 쓴다).
# fx가 op을 키에 넣지 않는 이유: 상회·하회 조건이 공존하면 환율이 어느 쪽으로
# 크게 움직여도 논거가 깨진다 → 반증 가능한 명제가 아니라 상시 경보다
# (2026-09-10 실측: 1,430원 상회 + 1,265원 하회가 한 분석에 동시 등록).
def cond_key(c: dict):
    ct, p = c.get("check_type"), c.get("params") or {}
    if ct == "flow":
        return (ct, p.get("investor"), p.get("direction"), p.get("metric"))
    if ct == "fx":
        return (ct,)
    if ct == "valuation":
        return (ct,)
    if ct == "earnings":
        return (ct, p.get("period"), p.get("metric"))
    if ct == "consensus":
        return (ct, p.get("year"), p.get("metric"))
    return (ct, str(c.get("조건", ""))[:40])


def dedupe_conditions(conditions: list[dict]) -> list[dict]:
    """같은 대상을 두 번 감시하는 조건을 제거한다 (먼저 온 것을 남긴다).

    본 분석 자체가 중복을 낼 수도 있어 반증 병합 전에도 한 번 거른다.
    """
    seen, out = set(), []
    for c in conditions:
        k = cond_key(c)
        if k in seen:
            continue
        seen.add(k)
        out.append(c)
    return out


def finalize_conditions(conditions: list[dict]) -> list[dict]:
    """캘리브레이션 후에도 임계가 비어 있는 자동 조건을 manual로 강등한다.

    임계는 앱이 분포에서 정하지만 표본(창 30개)이 모자라면 지어내지 않는다.
    그 경우 자동 감시 대상에서 빼되 서술은 남긴다 — flow_store의 부분합 위장
    금지와 같은 철학.
    """
    out = []
    for c in conditions:
        p = c.get("params") or {}
        unset = (c.get("check_type") == "flow"
                 and p.get("metric") == "cum_amount"
                 and not p.get("amount_eok"))
        if unset:
            out.append({"조건": c.get("조건", ""), "check_type": "manual",
                        "params": {"확인_방법": "상시 수급 확인"},
                        "spec_note": "임계 산출 표본 부족 (적재 창 30개 미만) — 수동 확인으로 강등"})
        else:
            out.append(c)
    return out


def downgrade_rejected(rejected: list[tuple[dict, str]]) -> list[dict]:
    """스크리닝 탈락 조건 → manual 강등 (서술은 보존, 자동 감시에서만 제외)."""
    return [
        {"조건": cond.get("조건", ""), "check_type": "manual",
         "params": {"확인_방법": "상시 뉴스/공시 확인"},
         "spec_note": f"조건 품질 결함 — {reason} (자동 감시 제외)"}
        for cond, reason in rejected
    ]


# ------------------------------------------------------------------ #
# 타입별 결정론 체크 — 반환 (state, detail)
# ------------------------------------------------------------------ #

def _fmt_eok(v_million: float) -> str:
    eok = v_million / 100
    if abs(eok) >= 10000:
        return f"{eok / 10000:+,.2f}조"
    return f"{eok:+,.0f}억"


def _check_flow(db, stock_code: str, p: dict) -> tuple[str, str]:
    """수급 조건 판정. 창은 확정 데이터만으로 채운다 (미확정 당일 행 제외)."""
    from app.services.watchlist.calibration import flow_series

    vals = flow_series(db, stock_code, p["investor"])
    if not vals:
        return "pending_data", "수급 적재 이력 없음 — 16:10 잡 축적 후 판정 가능"

    sign = -1 if p["direction"] == "sell" else 1
    label = ("외국인" if p["investor"] == "frgn" else "기관") + \
            (" 순매도" if p["direction"] == "sell" else " 순매수")
    days = p["days"]

    if p["metric"] == "consecutive_days":
        streak = 0
        for v in vals:
            if sign * v > 0:
                streak += 1
            else:
                break
        if streak >= days:
            return "triggered", f"{label} 연속 {streak}거래일 (기준 {days}거래일)"
        if streak == len(vals) and len(vals) < days:
            # 적재분 전체가 한 방향 — 그 이전이 확인 불가라 판정 보류 (부분 데이터로 단정 금지)
            return "pending_data", f"적재 {len(vals)}거래일 전부 {label} — {days}거래일 판정엔 커버리지 부족"
        return "ok", f"현재 {label} 연속 {streak}거래일 — 기준 {days}거래일 미달"

    # cum_amount
    if not p.get("amount_eok"):
        return "error", "누적 금액 임계 미확정 — 재분석 필요"
    if len(vals) < days:
        return "pending_data", f"확정 적재 {len(vals)}거래일 — {days}거래일 누적 판정은 커버리지 도달 후"
    cum = sum(vals[:days])
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

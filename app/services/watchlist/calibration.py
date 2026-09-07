"""무효화_조건 임계값 캘리브레이션 — 임계는 LLM이 아니라 앱이 정한다.

배경 (2026-09-07, SK하이닉스 실측):
    LLM이 낸 "외국인 5거래일 순매도 1.5조" 조건을 적재 수급 64거래일로 검증하니
    5일 롤링 창 60개 중 35개(58%)에서 충족됐다. 5일 누적 중앙값이 -1.93조라
    **평상시보다 나은 상태를 무효화 신호로 부르는** 조건이었다.
    반대로 "환율 1560원 상회"(현재 1346, 3개월 최고 1532)와 "영업이익률 50% 미만"
    (현재 76%)은 사실상 발동 불가였다.

원인은 LLM 능력이 아니라 역할 배분이다. LLM은 입력에 있는 숫자를 읽지만 서로
곱하고 나누지 않는다 — 분포를 계산해 임계를 잡는 건 애초에 앱의 일이다.

역할 분리:
    LLM  = 무엇을 감시할지 (투자자/방향/지표/기간 — 논거에서 도출되는 정성 판단)
    앱   = 얼마에서 켤지   (임계값 — 적재 분포에서 결정론 계산)

목표 발동률 {target:.0%} — 과거 창의 10%에서만 켜지는 지점을 임계로 잡는다.
계산 근거는 조건에 calibration 필드로 남겨 사후 검증 가능하게 한다.

한계 (명시하고 간다):
    수급 적재가 2026-07-02 시작이고 백필 불가라 표본이 얇다. 분포 기준 임계는
    레짐에 종속돼, 외인이 3개월 내내 판 구간에서는 p10 자체가 커져 임계가 느슨해진다.
    "최근 N거래일 대비 이례적"이라는 상대 기준임을 조건 텍스트에 박아 오독을 막는다.
    표본이 최소치에 못 미치면 임계를 지어내지 않고 캘리브레이션을 포기한다
    (부분 데이터로 단정 금지 — flow_store의 "부분합 위장 금지"와 같은 철학).
"""
import logging
import math

from sqlalchemy import select

logger = logging.getLogger(__name__)

TARGET_FIRE_RATE = 0.10   # 목표 발동률 — 과거 창의 10%에서만 켜지는 지점
FIRE_RATE_MAX = 0.30      # 이 이상 켜졌던 임계는 "평상시 수준" (게이트 상한)
MIN_WINDOWS = 30          # 분포 추정 최소 창 수 — 미달이면 캘리브레이션 포기
_FX_HORIZON_DAYS = 20     # 환율 임계 산정 지평 (약 1개월)
_FX_SIGMA = 2.0           # 목표 지평에서의 시그마 배수 (≈5% 확률의 단측 이동)
_FX_SIGMA_MAX = 3.5       # 이보다 먼 임계는 사실상 발동 불가 (게이트 상한)
_MARGIN_SIGMA = 1.5       # 분기 마진 임계 = 최근 마진 − 1.5σ(분기 변화폭)
_MARGIN_SIGMA_MAX = 3.0
_MIN_QUARTERS = 4         # 마진 변동성 추정 최소 분기 수


# ------------------------------------------------------------------ #
# 공통 통계 유틸 (외부 의존성 없음 — numpy 미사용)
# ------------------------------------------------------------------ #

def _quantile(sorted_vals: list[float], p: float) -> float:
    """선형보간 없는 단순 백분위 — 표본이 수십 개 수준이라 정밀도보다 예측 가능성 우선."""
    if not sorted_vals:
        raise ValueError("empty")
    idx = min(len(sorted_vals) - 1, max(0, int(round(p * (len(sorted_vals) - 1)))))
    return sorted_vals[idx]


def _stdev(vals: list[float]) -> float | None:
    if len(vals) < 2:
        return None
    mean = sum(vals) / len(vals)
    return math.sqrt(sum((v - mean) ** 2 for v in vals) / (len(vals) - 1))


def rolling_sums(vals: list[float], window: int) -> list[float]:
    """vals는 최신순. 반환은 각 시점의 window 거래일 누적."""
    if window <= 0 or len(vals) < window:
        return []
    return [sum(vals[i:i + window]) for i in range(len(vals) - window + 1)]


def fire_rate(sums: list[float], threshold_million: float, direction: str) -> float:
    """threshold(백만원, 양수)를 direction 방향으로 넘긴 창의 비율."""
    if not sums:
        return 0.0
    if direction == "sell":
        hit = sum(1 for s in sums if s <= -threshold_million)
    else:
        hit = sum(1 for s in sums if s >= threshold_million)
    return hit / len(sums)


def streak_rate(vals: list[float], days: int, direction: str) -> float:
    """days거래일 연속 동일방향이 관측된 시점의 비율."""
    if days <= 0 or len(vals) < days:
        return 0.0
    sign = -1 if direction == "sell" else 1
    windows = [vals[i:i + days] for i in range(len(vals) - days + 1)]
    hit = sum(1 for w in windows if all(sign * v > 0 for v in w))
    return hit / len(windows)


# ------------------------------------------------------------------ #
# 수급 시계열 — 확정 데이터만 (미확정 당일 행이 창을 먹지 않게)
# ------------------------------------------------------------------ #

def flow_series(db, stock_code: str, investor: str, limit: int = 260) -> list[float]:
    """최신순 순매수 금액(백만원). 결측일은 창에서 **제외**한다.

    주의: 당일 행이 미확정(NULL)으로 먼저 들어가는 경우가 있어, 결측을 자리만
    차지하게 두면 "5거래일 누적"이 실제로는 4거래일 누적이 된다 (2026-09-07 교정).
    """
    from app.models.investor_flow import InvestorFlowDaily

    attr = f"{investor}_ntby_amt"
    col = getattr(InvestorFlowDaily, attr)
    rows = db.execute(
        select(col)
        .where(InvestorFlowDaily.stock_code == stock_code, col.isnot(None))
        .order_by(InvestorFlowDaily.trade_date.desc())
        .limit(limit)
    ).scalars().all()
    return [float(v) for v in rows]


# ------------------------------------------------------------------ #
# 조건 텍스트 — 임계를 바꾸면 서술도 같이 바꾼다
# ------------------------------------------------------------------ #

def _fmt_eok(eok: float) -> str:
    if abs(eok) >= 10000:
        return f"{eok / 10000:,.1f}조원"
    return f"{eok:,.0f}억원"


def condition_text(check_type: str, p: dict) -> str:
    """params에서 조건 서술을 결정론으로 재생성 (임계 변경 시 텍스트-수치 불일치 방지)."""
    if check_type == "flow":
        who = "외국인" if p["investor"] == "frgn" else "기관"
        way = "순매도" if p["direction"] == "sell" else "순매수"
        if p["metric"] == "cum_amount":
            return (f"{who} {way} 누적이 {p['days']}거래일 동안 "
                    f"{_fmt_eok(p['amount_eok'])}을 넘어설 경우")
        return f"{who} {way}가 {p['days']}거래일 연속될 경우"
    if check_type == "fx":
        word = "상회" if p["op"] == "above" else "하회"
        return f"원/달러 환율이 {p['level']:,.0f}원을 {word}할 경우"
    if check_type == "valuation":
        word = "위로 올라설" if p["op"] == "above" else "아래로 내려갈"
        return f"PBR 5년 밴드 퍼센타일이 {p['value']:.0f}% {word} 경우"
    if check_type == "earnings":
        q = f"{p['period'][:4]}년 {int(p['period'][4:]) // 3}분기"
        label = {"op_margin_q_pct": "영업이익률", "op_yoy_pct": "영업이익 YoY",
                 "revenue_yoy_pct": "매출 YoY", "ni_yoy_pct": "순이익 YoY"}.get(
                     p["metric"], p["metric"])
        word = "아래로 내려갈" if p["op"] == "below" else "위로 올라설"
        return f"{q} {label}이 {p['value']:.1f}% {word} 경우"
    if check_type == "consensus":
        label = {"operating_profit": "영업이익", "eps": "EPS",
                 "revenue": "매출"}.get(p["metric"], p["metric"])
        return (f"{p['year']}년 {label} 컨센서스가 분석 시점 대비 "
                f"{p['drop_pct']:.0f}% 이상 하향될 경우")
    return ""


# ------------------------------------------------------------------ #
# 타입별 캘리브레이션 — 반환 (새 params, 근거 note) / 불가 시 None
# ------------------------------------------------------------------ #

def _calibrate_flow(db, stock_code: str, p: dict) -> tuple[dict, str] | None:
    vals = flow_series(db, stock_code, p["investor"])
    who = "외국인" if p["investor"] == "frgn" else "기관"

    if p["metric"] == "cum_amount":
        sums = rolling_sums(vals, p["days"])
        if len(sums) < MIN_WINDOWS:
            return None
        srt = sorted(sums)
        # sell이면 하위꼬리(가장 큰 순매도), buy면 상위꼬리
        q = _quantile(srt, TARGET_FIRE_RATE) if p["direction"] == "sell" \
            else _quantile(srt, 1 - TARGET_FIRE_RATE)
        # 꼬리가 조건 방향과 반대면(순매도 조건인데 p10조차 순매수) 임계를 지어내지 않는다.
        # abs()로 뒤집으면 방향이 뒤바뀐 작은 임계가 나와 상시 발동한다.
        if (p["direction"] == "sell" and q >= 0) or (p["direction"] == "buy" and q <= 0):
            return None
        amount_million = abs(q)
        eok = amount_million / 100
        step = 100 if eok >= 500 else 10          # 100억 / 10억 단위 반올림
        eok = max(step, round(eok / step) * step)
        new_p = dict(p, amount_eok=float(eok))
        rate = fire_rate(sums, eok * 100, p["direction"])
        old = _fmt_eok(p["amount_eok"])
        return new_p, (
            f"{who} {p['days']}거래일 누적 분포 {len(sums)}창(적재 {len(vals)}거래일)의 "
            f"p{TARGET_FIRE_RATE:.0%} 지점 → {_fmt_eok(eok)} (과거 발동률 {rate:.0%}). "
            f"LLM 원안 {old}은 발동률 {fire_rate(sums, p['amount_eok'] * 100, p['direction']):.0%}. "
            f"최근 {len(vals)}거래일 대비 상대 기준"
        )

    # consecutive_days — 목표 발동률 이하가 되는 최소 연속일수
    if len(vals) < MIN_WINDOWS:
        return None
    for d in range(2, 16):
        if streak_rate(vals, d, p["direction"]) <= TARGET_FIRE_RATE:
            if d == p["days"]:
                return None
            new_p = dict(p, days=d)
            return new_p, (
                f"{who} 연속 관측 분포(적재 {len(vals)}거래일)에서 발동률 "
                f"{TARGET_FIRE_RATE:.0%} 이하가 되는 최소 연속일수 {d}일 "
                f"(LLM 원안 {p['days']}일 발동률 "
                f"{streak_rate(vals, p['days'], p['direction']):.0%})"
            )
    return None


def _fx_sigma_move(fx: dict) -> float | None:
    """지평(_FX_HORIZON_DAYS) 기준 1σ 환율 변동폭(원). 스냅샷 파생값만 사용."""
    vol = fx.get("daily_vol_pct")
    cur = fx.get("current")
    if vol is None or not cur:
        return None
    return cur * (vol / 100) * math.sqrt(_FX_HORIZON_DAYS)


def _calibrate_fx(snapshot: dict, p: dict) -> tuple[dict, str] | None:
    fx = (snapshot or {}).get("fx_usdkrw") or {}
    cur = fx.get("current")
    if not fx.get("available") or cur is None:
        return None
    sigma = _fx_sigma_move(fx)
    if sigma is None:
        return None
    # 기준은 1개월 진폭 — 3개월 밴드는 추세 이동을 담고 있어 "밴드 밖"을 요구하면
    # 지평 대비 수 σ 떨어진 도달 불가 임계가 나온다 (2026-09-07: 현재 1346 / 3개월
    # 밴드 1346~1551 → 밴드 밖 요구가 곧 7.9σ).
    hi, lo = fx.get("high_1m"), fx.get("low_1m")
    if p["op"] == "above":
        level = cur + _FX_SIGMA * sigma
        if hi is not None:
            level = max(level, hi + sigma * 0.25)   # 최근 진폭 안은 이례 신호가 아니다
    else:
        level = cur - _FX_SIGMA * sigma
        if lo is not None:
            level = min(level, lo - sigma * 0.25)
    level = round(level / 5) * 5                    # 5원 단위
    if abs(level - p["level"]) < 5:
        return None
    band = f"1개월 진폭 {lo:,.0f}~{hi:,.0f} 밖" if lo and hi else "1개월 진폭 미상"
    return dict(p, level=float(level)), (
        f"현재 {cur:,.0f}원, 일간 변동성 {fx['daily_vol_pct']:.2f}% → "
        f"{_FX_HORIZON_DAYS}거래일 1σ ≈ {sigma:,.0f}원. "
        f"{_FX_SIGMA:.0f}σ 지점 {level:,.0f}원 ({band}). "
        f"LLM 원안 {p['level']:,.0f}원은 현재 대비 "
        f"{abs(p['level'] - cur) / sigma:.1f}σ"
    )


def _margin_history(snapshot: dict) -> list[float]:
    quarters = ((snapshot or {}).get("fundamentals_quarterly") or {}).get("income_single_q") or []
    return [q["op_margin_q_pct"] for q in quarters
            if isinstance(q.get("op_margin_q_pct"), (int, float))]


def margin_sigma(snapshot: dict) -> tuple[float, float] | None:
    """(최근 분기 영업이익률, 분기간 변화폭 σ) — 없으면 None."""
    margins = _margin_history(snapshot)
    if len(margins) < _MIN_QUARTERS:
        return None
    diffs = [margins[i] - margins[i + 1] for i in range(len(margins) - 1)]
    sd = _stdev(diffs)
    if sd is None or sd <= 0:
        return None
    return margins[0], sd


def _calibrate_earnings(snapshot: dict, p: dict) -> tuple[dict, str] | None:
    if p["metric"] != "op_margin_q_pct" or p["op"] != "below":
        return None  # YoY 지표는 값이 아니라 지표 선택이 문제 — 게이트가 처리
    ms = margin_sigma(snapshot)
    if ms is None:
        return None
    latest, sd = ms
    value = round(latest - _MARGIN_SIGMA * sd, 1)
    if value <= 0 or abs(value - p["value"]) < 0.5:
        return None
    return dict(p, value=float(value)), (
        f"최근 분기 영업이익률 {latest:.1f}%, 분기간 변화폭 σ {sd:.1f}%p → "
        f"{_MARGIN_SIGMA}σ 악화 지점 {value:.1f}% "
        f"(LLM 원안 {p['value']:.1f}%는 {abs(latest - p['value']) / sd:.1f}σ)"
    )


def _calibrate_valuation(snapshot: dict, p: dict) -> tuple[dict, str] | None:
    if p["op"] != "above":
        return None  # below는 방향 자체가 틀림 — 게이트가 탈락시킨다
    band = (((snapshot or {}).get("valuation_current") or {}).get("pbr_band_5y") or {})
    if not band.get("available"):
        return None
    cur = band.get("pbr_percentile_5y")
    if cur is None:
        return None
    value = float(min(90, max(50, round(cur + 25))))
    # 이미 그 위에 있으면(예: 현재 98퍼센타일) 어떤 above 임계도 즉시 충족이라
    # 캘리브레이션이 오히려 "이미 충족" 조건을 만든다 — 게이트가 탈락시키게 둔다.
    if value <= cur or abs(value - p["value"]) < 5:
        return None
    return dict(p, value=value), (
        f"현재 PBR 5년 퍼센타일 {cur:.0f}% → +25%p 지점 {value:.0f}% "
        f"(LLM 원안 {p['value']:.0f}%)"
    )


# ------------------------------------------------------------------ #
# 진입점
# ------------------------------------------------------------------ #

def calibrate_conditions(db, stock_code: str, snapshot: dict | None,
                         conditions: list[dict]) -> tuple[list[dict], list[str]]:
    """정규화된 조건의 수치 임계를 앱 계산값으로 교체.

    - 감시 대상(투자자/방향/지표/기간)은 LLM 판단을 그대로 둔다.
    - 계산 근거는 cond["calibration"]에 남기고, 조건 서술도 새 임계에 맞춰 재생성한다.
    - 표본 부족·데이터 결측이면 손대지 않는다 (임계를 지어내지 않음).
    반환: (조건 목록, 변경 로그)
    """
    out, notes = [], []
    for cond in conditions:
        ct = cond.get("check_type")
        p = cond.get("params") or {}
        try:
            if ct == "flow":
                res = _calibrate_flow(db, stock_code, p)
            elif ct == "fx":
                res = _calibrate_fx(snapshot, p)
            elif ct == "earnings":
                res = _calibrate_earnings(snapshot, p)
            elif ct == "valuation":
                res = _calibrate_valuation(snapshot, p)
            else:
                res = None
        except Exception as e:  # 캘리브레이션 실패가 분석을 죽이지 않는다
            logger.warning("calibration failed (%s, %s): %s", stock_code, ct, e)
            res = None

        if not res:
            out.append(cond)
            continue
        new_p, note = res
        new_cond = dict(cond, params=new_p,
                        조건=condition_text(ct, new_p) or cond.get("조건", ""),
                        calibration={"원안": p, "근거": note})
        out.append(new_cond)
        notes.append(f"[{ct}] {note}")
    return out, notes

"""
규칙 기반 종목 선정 (Gemini 호출 없음).

목적: **AI 픽의 대조군**. 랜덤 벤치마크보다 엄격한 기준으로
"Gemini가 단순 규칙 대비 값을 하는가"를 판정하기 위한 선정 로직.

설계 원칙:
- 파라미터·유니버스·청산 규칙은 비교 대상 AI 전략과 완전히 동일하게 두고,
  **변수는 '선정 방식' 하나만** 다르게 한다.
- 입력은 StrategyRunner가 이미 수집한 stock_data를 그대로 쓴다 (추가 API 호출 0).
- **판정 기준값은 `close_asof`(최근 완결 봉 종가)를 쓴다.** `current_price`는 장중엔
  실시간가라 실행 시각에 따라 결과가 달라진다 — 2026-10-09 실측에서 장전(08:30) 실행
  8런이 전부 0픽이고 재시작 캐치업으로 장중 실행된 3런만 발동했다. 원인은
  `close_high_20d` 창에 비교 대상인 전일 종가가 끼어 신고가 판정이 구조적으로
  성립 불가였던 것(client.py `_get_domestic_stock_info` 주석 참조).
- 조건 충족 종목이 pick_count에 못 미치면 **모자란 채로 반환**한다.
  억지로 채우면 규칙의 의미가 사라진다 (Stage4 B-gate와 같은 철학).
"""
import logging

logger = logging.getLogger(__name__)

RULE_MODES = ("rule_breakout", "rule_oversold")

# 과매도 반등 임계값 — 강세장에서 발동이 0건이 되지 않도록 보수적으로 시작.
# 데이터가 쌓이면 조인다 (RSI 30 / 거래대금 2배는 KOSPI200에서 몇 주씩 0건).
_OVERSOLD_RSI_MAX = 35.0
_OVERSOLD_TURNOVER_MIN = 1.5


def is_rule_mode(selection_mode: str | None) -> bool:
    return (selection_mode or "") in RULE_MODES


def _ref_close(stock: dict) -> float | None:
    """규칙 판정 기준 종가 — 최근 완결 봉 종가. 구 스냅샷 호환으로 current_price 폴백."""
    return stock.get("close_asof") or stock.get("current_price")


def _ref_ma5(stock: dict) -> float | None:
    """완결 봉 기준 MA5. 폴백은 기존 ma5(당일 미확정 봉 포함 가능)."""
    return stock.get("ma5_asof") or stock.get("ma5")


def _pick(stock: dict, rank: int, reason: str) -> dict:
    """Stage4 픽과 동일한 형태로 변환 (저장·검증 경로를 그대로 태우기 위함)."""
    return {
        "stock_code": stock.get("stock_code", ""),
        "stock_name": stock.get("stock_name", ""),
        "ai_reason": reason,
        "historical_basis": None,
        "risk_factors": None,
        "rank": rank,
    }


def select_breakout(stock_data: list[dict], pick_count: int) -> list[dict]:
    """20일 신고가 돌파 모멘텀.

    조건 (전부 AND):
      - 종가가 직전 20거래일 종가 최고를 갱신
      - 종가 > 5일 이동평균
    동점 정렬: 20일 최고 대비 돌파 폭이 큰 순
    """
    hits = []
    for s in stock_data:
        price  = _ref_close(s)
        high20 = s.get("close_high_20d")
        ma5    = _ref_ma5(s)
        if not price or not high20 or not ma5:
            continue
        if price > high20 and price > ma5:
            hits.append((price / high20 - 1, s))

    hits.sort(key=lambda x: -x[0])
    return [
        _pick(s, i + 1, f"20일 신고가 돌파 ({s.get('asof_date') or '기준일 미상'} 종가 "
                        f"{int(_ref_close(s)):,}원, 직전 최고 {int(s['close_high_20d']):,}원 대비 "
                        f"+{gap * 100:.1f}%), 종가 > MA5({int(_ref_ma5(s)):,}원)")
        for i, (gap, s) in enumerate(hits[:pick_count])
    ]


def select_oversold(stock_data: list[dict], pick_count: int) -> list[dict]:
    """과매도 반등.

    조건 (전부 AND):
      - RSI(14) ≤ 35
      - 당일 거래대금 ≥ 전일 거래대금 × 1.5
    동점 정렬: RSI 낮은 순
    """
    hits = []
    for s in stock_data:
        rsi = s.get("rsi_14")
        ratio = s.get("turnover_ratio")
        if rsi is None or ratio is None:
            continue
        if rsi <= _OVERSOLD_RSI_MAX and ratio >= _OVERSOLD_TURNOVER_MIN:
            hits.append((rsi, s))

    hits.sort(key=lambda x: x[0])
    return [
        _pick(s, i + 1, f"과매도 반등 후보 — RSI {rsi:.1f} (≤{_OVERSOLD_RSI_MAX:g}), "
                        f"거래대금 전일 대비 {s['turnover_ratio']:.1f}배")
        for i, (rsi, s) in enumerate(hits[:pick_count])
    ]


def select_by_rule(selection_mode: str, stock_data: list[dict], pick_count: int) -> list[dict]:
    """selection_mode에 맞는 규칙 선정기 실행."""
    selector = {
        "rule_breakout": select_breakout,
        "rule_oversold": select_oversold,
    }[selection_mode]

    picks = selector(stock_data, pick_count)
    logger.info(
        "Rule selection [%s]: %d/%d picks from %d candidates",
        selection_mode, len(picks), pick_count, len(stock_data),
    )
    if not picks:
        logger.info("Rule selection [%s]: 조건 충족 종목 없음 — 픽 0개 (정상 동작)", selection_mode)
    return picks

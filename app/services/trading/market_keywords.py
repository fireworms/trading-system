"""
매크로 서술(market_theme) 기반 시장 판단 키워드 — 단일 관리 지점.

두 레이어가 서로 다른 강도로 같은 문자열을 본다. 예전에는 리스트가
runner와 executor에 따로 박혀 있어 조용히 드리프트했다:

  A-gate (runner)      = "너무 나빠서 분석조차 하지 않는다" → Stage4 스킵
  매수 감액 (executor) = "분석은 했지만 조심한다"          → 매수금 절반

**두 리스트를 같게 만들면 안 된다.** A-gate가 먼저(08:30) 같은 market_theme를
평가해 Stage4를 스킵하면 추천이 0개가 되고, 09:20 executor는 살 게 없어진다.
즉 리스트가 동일하면 감액 로직은 영원히 도달 불가능한 죽은 코드가 된다.
감액이 의미를 가지려면 **A-gate가 잡지 않는 더 약한 신호**를 잡아야 한다.

2026-09-03 정리: 통합이 아니라 '역할 분리 + 단일 관리 지점'으로 결론.
"""

# A-gate: 이 키워드가 market_theme에 있으면 Stage4를 스킵한다 (검증 20건+ 시 활성)
BEAR_KEYWORDS = [
    "하락장", "폭락", "급락", "약세", "하락세", "조정장",
    "침체", "위기", "crash", "bear", "매도세",
]

# 매수 감액: A-gate를 통과했지만 경계가 필요한 약한 신호 (매수금 50%)
#
# 예전 executor 리스트에 있던 "위험"·"하락"은 제거했다 — 한국 시장 서술에서
# "위험선호 회복", "위험자산 선호 개선", "하락 압력 완화"는 오히려 강세 표현이라
# 강세 국면에 매수금을 반토막 내는 오탐이 난다. 부분 문자열 매칭이라 더 위험하다.
CAUTION_KEYWORDS = [
    "불확실", "경계", "관망", "변동성 확대", "부담", "차익실현", "혼조",
]


def is_bearish(market_theme: str | None) -> bool:
    """A-gate 판정 — Stage4를 스킵할 정도의 하락 신호인가."""
    if not market_theme:
        return False
    t = market_theme.lower()
    return any(kw in t for kw in BEAR_KEYWORDS)


def is_cautious(market_theme: str | None) -> bool:
    """매수 감액 판정 — 분석은 했지만 조심할 신호인가.

    BEAR_KEYWORDS도 함께 본다: A-gate가 데이터 부족(_GATE_MIN_DATA 미달)으로
    비활성인 동안에는 하락 테마에서도 추천이 생성되므로, 그때는 감액이라도 걸어야 한다.
    """
    if not market_theme:
        return False
    t = market_theme.lower()
    return any(kw in t for kw in CAUTION_KEYWORDS) or any(kw in t for kw in BEAR_KEYWORDS)

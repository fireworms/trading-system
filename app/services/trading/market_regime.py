"""
시장 국면 판정 (KOSPI 20일 이동평균 위/아래).

전략 성과가 종목 선정 탓인지 시장 국면 탓인지 분리해서 보기 위한 **기록 전용** 모듈.
매매 차단·필터링에는 쓰지 않는다 (그건 A-gate / morning_gate / 듀얼시그널 담당).

판정 결과와 원본값(종가·MA20)을 함께 저장해 나중에 기준(20일 → 60일 등)을
바꿔도 소급 재계산이 가능하게 한다.
"""
import logging
from datetime import date

logger = logging.getLogger(__name__)

_KOSPI = "0001"
MA_PERIOD = 20


def fetch_kospi_history(client, days: int) -> list[tuple[str, float]]:
    """KOSPI 일봉 (YYYYMMDD, 종가) 최신순.

    소급 계산 시 run마다 다시 긁지 않도록 **한 번만 호출해 재사용**할 것.
    """
    return client.get_index_daily_series(_KOSPI, days=days)


def regime_from_history(
    history: list[tuple[str, float]], as_of: date, ma_period: int = MA_PERIOD
) -> dict | None:
    """as_of(분석 실행일) 기준 국면 판정.

    as_of 당일 봉은 제외한다 — 분석 잡은 08:30(개장 전) 실행이라
    그 시점에 확정된 최신 종가는 전 거래일 종가다. 미래 정보 유입 방지.
    반환: {"close", "ma20", "state"} 또는 데이터 부족 시 None
    """
    cutoff = as_of.strftime("%Y%m%d")
    past = [(d, c) for d, c in history if d < cutoff]   # history는 최신순
    if len(past) < ma_period:
        return None

    close = past[0][1]
    ma20 = sum(c for _, c in past[:ma_period]) / ma_period
    return {
        "close": round(close, 2),
        "ma20":  round(ma20, 2),
        "state": "above" if close >= ma20 else "below",
    }


def compute_regime(client, as_of: date | None = None) -> dict | None:
    """라이브 경로 — 필요한 만큼만 조회해 국면 판정. 실패 시 None (분석은 계속 진행)."""
    as_of = as_of or date.today()
    try:
        history = fetch_kospi_history(client, MA_PERIOD + 5)   # 여유분 = 휴장일 보정
        return regime_from_history(history, as_of)
    except Exception as e:
        logger.warning("Market regime computation failed for %s: %s", as_of, e)
        return None

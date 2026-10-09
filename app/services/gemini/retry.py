"""
Gemini 호출 일시 오류 재시도 — 호출부 공용 헬퍼.

배경: 503(UNAVAILABLE, 모델 과부하)·429(쿼터)·5xx는 **잠시 뒤 재시도하면 대개 성공**하는
일시 오류다. 그런데 재시도가 호출부마다 따로 박혀 있어서 편차가 생겼다 —
check_news는 20초 후 1회 재시도, 모닝 게이트·thesis 재검증은 **1회 시도 후 즉시 실패**.
모닝 게이트가 503 하나로 날아가면 그날 야간 리스크 평가 없이 매수가 진행된다
(수치 게이트(QQQ)가 남아 있어 완전 무방비는 아니지만, 지정학·선물 판단이 빠진다).

재시도 로직을 두 곳 이상에 복제하면 한쪽만 드리프트한다 — 청산 모델을
`verifier.simulate_exit_pnl` 하나로 모은 것과 같은 이유로 여기에 모은다.

재시도 대상 (`is_transient`):
- 429 / 500 / 502 / 503 / 504 + UNAVAILABLE·overloaded·RESOURCE_EXHAUSTED·DEADLINE 류
- 응답 형태 오류(JSON 파싱 실패 등) — 같은 프롬프트라도 다음 응답은 다를 수 있다

재시도 **안 하는** 것: 400/401/403/404 같은 요청·인증 오류. 기다려도 안 고쳐지고,
스케줄러 잡을 붙잡고 있을 이유가 없다.
"""
import json
import logging
import random
import time
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# 기다려도 안 고쳐지는 코드 (요청·인증 문제)
_PERMANENT_CODES = {400, 401, 403, 404, 405, 422}
_TRANSIENT_CODES = {408, 409, 425, 429, 500, 502, 503, 504}
_TRANSIENT_MARKERS = (
    "unavailable", "overloaded", "resource_exhausted", "deadline",
    "internal error", "try again", "temporarily",
)


def is_transient(exc: BaseException) -> bool:
    """기다렸다 다시 하면 풀릴 가능성이 있는 오류인가."""
    code = getattr(exc, "code", None)
    if isinstance(code, int):
        if code in _PERMANENT_CODES:
            return False
        if code in _TRANSIENT_CODES:
            return True
    # 응답이 JSON이 아니거나 잘려 온 경우 — 다음 응답은 다를 수 있다
    if isinstance(exc, (json.JSONDecodeError, ValueError, KeyError, TypeError)):
        return True
    text = f"{getattr(exc, 'status', '')} {exc}".lower()
    return any(m in text for m in _TRANSIENT_MARKERS)


def call_with_retry(
    fn: Callable[[], T],
    *,
    label: str,
    attempts: int = 3,
    delays: tuple[float, ...] = (20.0, 60.0),
) -> T:
    """fn()을 일시 오류에 대해 재시도하며 호출한다.

    delays[i] = i번째 실패 후 대기 초. attempts보다 짧으면 마지막 값을 반복 사용한다.
    대기에 ±20% 지터를 섞는다 — 여러 잡이 같은 분에 몰려 실패하면 동시에 재시도해
    과부하를 다시 때리게 된다.

    최종 실패 시 마지막 예외를 그대로 올린다 (호출부의 기존 실패 처리 유지).
    """
    last: BaseException | None = None
    for i in range(max(1, attempts)):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 — 호출부가 예외 종류를 보지 않는다
            last = e
            transient = is_transient(e)
            logger.warning(
                "%s failed (attempt %d/%d, transient=%s): %s",
                label, i + 1, attempts, transient, e,
            )
            if not transient or i == attempts - 1:
                break
            delay = delays[min(i, len(delays) - 1)] if delays else 0.0
            delay *= 1 + random.uniform(-0.2, 0.2)
            logger.info("%s: retrying in %.0fs", label, delay)
            time.sleep(delay)
    assert last is not None
    raise last

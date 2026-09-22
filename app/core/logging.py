"""앱 로깅 설정 — 단일 진입점.

배경: uvicorn은 자기 로거(uvicorn/uvicorn.access)만 INFO로 올리고 **루트 로거는
건드리지 않는다**. 앱 모듈은 전부 `logging.getLogger(__name__)`이라 루트 기본값
WARNING이 유효 레벨이 되고, 그 결과 `RT closed`·`Monitoring N positions` 같은
정상 동작 기록이 저널에 아예 안 남았다. 2026-09-08 청산 23,990회 실패가 늦게
발견된 배경 중 하나 — ERROR였기에 그나마 보였다.

핸들러는 붙이지 않고 루트 레벨만 올린다(=stderr 기본 핸들러 1개). journald가
줄마다 타임스탬프를 찍으므로 포맷에 asctime을 넣지 않는다(이중 표기 방지).
"""
import logging

from app.core.config import get_settings

# 호출당 1줄씩 찍어 저널을 덮는 라이브러리 — KIS는 초당 18콜이라 INFO면 앱 로그가 묻힌다
_NOISY = ("httpx", "httpcore", "websockets", "google_genai", "google.genai",
          "urllib3", "asyncio")

_configured = False


def setup_logging() -> None:
    """루트 로거 레벨 설정 + 소음 라이브러리 억제. 앱 기동 시 1회 호출 (멱등)."""
    global _configured
    if _configured:
        return

    s = get_settings()
    level = getattr(logging, s.log_level.upper(), logging.INFO)

    # force=False: uvicorn이 이미 자기 핸들러를 붙였으면 건드리지 않는다.
    # uvicorn 로거는 propagate=False라 루트 핸들러와 중복 출력되지 않는다.
    logging.basicConfig(level=level, format="%(levelname)s [%(name)s] %(message)s")
    logging.getLogger().setLevel(level)

    for name in _NOISY:
        logging.getLogger(name).setLevel(logging.WARNING)

    # 프론트 폴링 액세스 로그가 앱 로그를 덮는 경우 .env에서 LOG_ACCESS=false
    if not s.log_access:
        logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    _configured = True
    logging.getLogger(__name__).info(
        "Logging configured: level=%s access_log=%s", s.log_level.upper(), s.log_access)

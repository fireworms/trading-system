"""
asyncio 이벤트 루프를 APScheduler 백그라운드 스레드와 공유하기 위한 모듈.
lifespan 시작 시 set_loop()를 호출하고, 동기 컨텍스트에서 get_loop()로 참조한다.
"""
import asyncio

_loop: asyncio.AbstractEventLoop | None = None


def set_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _loop
    _loop = loop


def get_loop() -> asyncio.AbstractEventLoop | None:
    return _loop

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import users, strategies, recommendations, positions, market, admin, prompt_versions, stock_master, backtest
from app.api import ws as ws_api
import app.models.app_config  # noqa: F401 — Alembic autogenerate 인식용


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.services.trading.scheduler import start_scheduler, stop_scheduler, run_startup_catchup
    start_scheduler()
    run_startup_catchup()

    # KIS 실시간 클라이언트 초기화
    _init_realtime_client()

    yield
    stop_scheduler()
    # 실시간 클라이언트 종료
    from app.services.kis.realtime import get_realtime_client
    rt = get_realtime_client()
    if rt:
        rt.stop()


def _init_realtime_client() -> None:
    """DB에서 첫 번째 활성 브로커 계좌로 실시간 클라이언트 초기화."""
    try:
        from app.core.database import SessionLocal
        from app.services.kis.client import get_kis_client
        from app.services.kis.realtime import init_realtime_client
        from app.api.ws import manager as ws_manager

        with SessionLocal() as db:
            kis = get_kis_client(db)
            if not kis:
                return
            rt = init_realtime_client(kis._key, kis._secret, kis._is_real)

        # KIS 가격 업데이트 → WS 브로드캐스트 연결
        async def _on_price(code: str, price: dict) -> None:
            await ws_manager.broadcast(code, price)

        rt.add_callback(_on_price)
        rt.start()
    except Exception as e:
        import logging
        logging.getLogger(__name__).error("Realtime client init failed: %s", e)


app = FastAPI(
    title="Trading System API",
    description="KIS + Gemini AI 기반 자동매매 시스템",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(users.router, prefix="/api/v1")
app.include_router(strategies.router, prefix="/api/v1")
app.include_router(recommendations.router, prefix="/api/v1")
app.include_router(positions.router, prefix="/api/v1")
app.include_router(market.router, prefix="/api/v1")
app.include_router(admin.router, prefix="/api/v1")
app.include_router(prompt_versions.router, prefix="/api/v1")
app.include_router(stock_master.router, prefix="/api/v1")
app.include_router(backtest.router, prefix="/api/v1")
app.include_router(ws_api.router)   # WebSocket은 prefix 없이 /ws/prices


@app.get("/health")
def health():
    return {"status": "ok"}

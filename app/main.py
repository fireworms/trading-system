from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import users, strategies, recommendations, positions, market, admin, prompt_versions, stock_master
import app.models.app_config  # noqa: F401 — Alembic autogenerate 인식용


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.services.trading.scheduler import start_scheduler, stop_scheduler, run_startup_catchup
    start_scheduler()
    run_startup_catchup()   # 누락된 스케줄 작업 백그라운드 보완
    yield
    stop_scheduler()


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


@app.get("/health")
def health():
    return {"status": "ok"}

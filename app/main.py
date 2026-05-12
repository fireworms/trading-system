from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import users, strategies, recommendations, positions, market, admin, prompt_versions, stock_master, backtest
from app.api import ws as ws_api
import app.models.app_config   # noqa: F401 — Alembic autogenerate 인식용
import app.models.news_event    # noqa: F401


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
            # hts_id가 등록된 경우 체결통보 구독
            from app.models.user import BrokerAccount
            from sqlalchemy import select as _select
            account = db.scalar(_select(BrokerAccount).where(BrokerAccount.is_active == True).limit(1))
            hts_id = account.hts_id if account else None

        # 가격 업데이트 → WS 브로드캐스트
        async def _on_price(code: str, price: dict) -> None:
            await ws_manager.broadcast(code, price)

        # 체결 통보 → entry_price 즉시 업데이트
        async def _on_execution(data: dict) -> None:
            if data.get("side") != "buy":
                return
            import asyncio
            from datetime import date
            from decimal import Decimal
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, _update_fill_price, data["stock_code"], data["fill_price"])

        rt.add_callback(_on_price)
        rt.add_execution_callback(_on_execution)
        if hts_id:
            rt.subscribe_execution(hts_id)
            logger.info("KIS execution notification enabled (hts_id=%s)", hts_id)
        else:
            logger.info("KIS execution notification disabled (hts_id not set)")
        rt.start()
    except Exception as e:
        import logging
        logging.getLogger(__name__).error("Realtime client init failed: %s", e)


def _update_fill_price(stock_code: str, fill_price: int) -> None:
    """체결 직후 오늘 해당 종목 HOLDING 포지션의 entry_price/peak_price 업데이트."""
    import logging
    from datetime import date
    from decimal import Decimal
    from sqlalchemy import select
    from app.core.database import SessionLocal
    from app.models.position import Position, PositionStatus

    log = logging.getLogger(__name__)
    try:
        with SessionLocal() as db:
            pos = db.scalar(
                select(Position).where(
                    Position.stock_code == stock_code,
                    Position.status == PositionStatus.HOLDING,
                    Position.entry_date == date.today(),
                ).limit(1)
            )
            if pos:
                price = Decimal(str(fill_price))
                pos.entry_price = price
                pos.peak_price  = price
                db.commit()
                log.info("Fill price updated: %s entry=%s", stock_code, price)
    except Exception as e:
        log.error("Fill price update failed: %s", e)


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

"""
실시간 가격 WebSocket 엔드포인트.

클라이언트 → 서버: {"type": "subscribe", "codes": ["005930", "000660"]}
서버 → 클라이언트: {"type": "price", "code": "005930", "current_price": 75000, "change": 500, "change_pct": 0.67, "volume": 12345678}
"""
import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)
router = APIRouter(tags=["websocket"])


class PriceStreamManager:
    def __init__(self):
        # WebSocket → 구독 중인 코드 집합
        self._clients:      dict[WebSocket, set[str]] = {}
        # 코드 → 구독 중인 WebSocket 집합
        self._code_clients: dict[str, set[WebSocket]] = {}

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients[ws] = set()
        logger.info("WS client connected. total=%d", len(self._clients))

    async def disconnect(self, ws: WebSocket) -> None:
        codes = self._clients.pop(ws, set())
        for code in codes:
            self._code_clients.get(code, set()).discard(ws)
            await self._maybe_unsubscribe_kis(code)
        logger.info("WS client disconnected. total=%d", len(self._clients))

    async def subscribe(self, ws: WebSocket, codes: list[str]) -> None:
        from app.services.kis.realtime import get_realtime_client

        old_codes = self._clients.get(ws, set())
        new_codes = set(codes)

        # 제거된 코드 처리
        for code in old_codes - new_codes:
            self._code_clients.get(code, set()).discard(ws)
            await self._maybe_unsubscribe_kis(code)

        # 추가된 코드 처리
        self._clients[ws] = new_codes
        rt = get_realtime_client()
        for code in new_codes:
            self._code_clients.setdefault(code, set()).add(ws)
            if rt:
                await rt.subscribe(code)

        # 현재 캐시된 가격 즉시 전송
        if rt:
            for code in new_codes:
                price = rt.get_price(code)
                if price:
                    try:
                        await ws.send_json({"type": "price", "code": code, **price})
                    except Exception:
                        pass

    async def _maybe_unsubscribe_kis(self, code: str) -> None:
        """해당 코드 구독자가 0명이면 KIS 구독 해제."""
        from app.services.kis.realtime import get_realtime_client
        if not self._code_clients.get(code):
            rt = get_realtime_client()
            if rt:
                await rt.unsubscribe(code)

    async def broadcast(self, code: str, price_data: dict) -> None:
        clients = list(self._code_clients.get(code, set()))
        disconnected = []
        for ws in clients:
            try:
                await ws.send_json({"type": "price", "code": code, **price_data})
            except Exception:
                disconnected.append(ws)
        for ws in disconnected:
            await self.disconnect(ws)


manager = PriceStreamManager()


@router.websocket("/ws/prices")
async def prices_ws(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_json()
            if data.get("type") == "subscribe":
                await manager.subscribe(websocket, data.get("codes", []))
            elif data.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        await manager.disconnect(websocket)
    except Exception as e:
        logger.error("WS error: %s", e)
        await manager.disconnect(websocket)

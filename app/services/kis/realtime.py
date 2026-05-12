"""
KIS 실시간 체결가 WebSocket 클라이언트.

흐름:
  1. REST API로 approval_key 발급
  2. KIS WebSocket 연결 (ws://ops.koreainvestment.com:21000)
  3. 종목 코드 subscribe
  4. 체결가 수신 → 콜백 호출
  5. 연결 끊기면 재연결 (최대 60초 백오프)
"""
import asyncio
import json
import logging
from typing import Awaitable, Callable

import httpx
import websockets

logger = logging.getLogger(__name__)

_WS_REAL    = "ws://ops.koreainvestment.com:21000"
_WS_PAPER   = "ws://ops.koreainvestment.com:31000"
_BASE_REAL  = "https://openapi.koreainvestment.com:9443"
_BASE_PAPER = "https://openapivts.koreainvestment.com:29443"

PriceCallback     = Callable[[str, dict], Awaitable[None]]
ExecutionCallback = Callable[[dict], Awaitable[None]]


class KISRealtimeClient:
    def __init__(self, app_key: str, app_secret: str, is_real: bool = True):
        self._key     = app_key
        self._secret  = app_secret
        self._is_real = is_real
        self._base    = _BASE_REAL if is_real else _BASE_PAPER
        self._ws_url  = _WS_REAL   if is_real else _WS_PAPER

        self._approval_key: str | None = None
        self._ws = None
        self._subscribed: set[str] = set()
        self._exec_cano: str | None = None          # 체결통보 구독용 CANO
        self._prices: dict[str, dict] = {}
        self._callbacks: list[PriceCallback] = []
        self._exec_callbacks: list[ExecutionCallback] = []
        self._running   = False
        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------------ #
    # 외부 인터페이스
    # ------------------------------------------------------------------ #

    def add_callback(self, cb: PriceCallback) -> None:
        self._callbacks.append(cb)

    def add_execution_callback(self, cb: ExecutionCallback) -> None:
        self._exec_callbacks.append(cb)

    def subscribe_execution(self, cano: str) -> None:
        """체결통보(H0STCNI0) 구독. cano = 계좌번호 앞 8자리."""
        self._exec_cano = cano

    def get_price(self, code: str) -> dict | None:
        return self._prices.get(code)

    def get_all_prices(self) -> dict[str, dict]:
        return dict(self._prices)

    async def subscribe(self, code: str) -> None:
        self._subscribed.add(code)
        if self._ws and not self._is_closed():
            await self._send_sub(code, subscribe=True)

    async def unsubscribe(self, code: str) -> None:
        self._subscribed.discard(code)
        self._prices.pop(code, None)
        if self._ws and not self._is_closed():
            await self._send_sub(code, subscribe=False)

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()

    # ------------------------------------------------------------------ #
    # 내부 구현
    # ------------------------------------------------------------------ #

    def _is_closed(self) -> bool:
        return self._ws is None or self._ws.close_code is not None

    async def _get_approval_key(self) -> str:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{self._base}/oauth2/Approval",
                json={
                    "grant_type": "client_credentials",
                    "appkey":     self._key,
                    "secretkey":  self._secret,
                },
            )
            resp.raise_for_status()
            return resp.json()["approval_key"]

    async def _run_loop(self) -> None:
        backoff = 5
        while self._running:
            try:
                await self._connect_and_receive()
                backoff = 5
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("KIS WS error: %s — reconnect in %ds", e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    async def _connect_and_receive(self) -> None:
        if not self._approval_key:
            self._approval_key = await self._get_approval_key()

        async with websockets.connect(self._ws_url, ping_interval=None) as ws:
            self._ws = ws
            logger.info("KIS WS connected: %s", self._ws_url)

            # 재연결 시 기존 구독 복원
            for code in list(self._subscribed):
                await self._send_sub(code, subscribe=True)

            # 체결통보 구독
            if self._exec_cano:
                await self._send_raw_sub("H0STCNI0", self._exec_cano, subscribe=True)
                logger.info("KIS execution notification subscribed: cano=%s", self._exec_cano)

            async for raw in ws:
                await self._handle(raw)

    async def _send_sub(self, code: str, subscribe: bool) -> None:
        await self._send_raw_sub("H0STCNT0", code, subscribe)

    async def _send_raw_sub(self, tr_id: str, tr_key: str, subscribe: bool) -> None:
        if not self._ws:
            return
        msg = {
            "header": {
                "approval_key": self._approval_key,
                "custtype":     "P",
                "tr_type":      "1" if subscribe else "2",
                "content-type": "utf-8",
            },
            "body": {
                "input": {
                    "tr_id":  tr_id,
                    "tr_key": tr_key,
                }
            },
        }
        await self._ws.send(json.dumps(msg))

    async def _handle(self, raw: str) -> None:
        # PINGPONG 처리
        if raw == "PINGPONG":
            if self._ws:
                await self._ws.send("PONG")
            return

        # 제어 메시지 (JSON) — 구독 확인 응답 등
        if raw.startswith("{"):
            return

        # 데이터 메시지: "0|TR_ID|건수|필드^필드^..."
        parts = raw.split("|")
        if len(parts) < 4:
            return

        tr_id = parts[1]
        count = int(parts[2]) if parts[2].isdigit() else 1
        body  = parts[3]
        fields_per = len(body.split("^")) // count

        for i in range(count):
            f = body.split("^")[i * fields_per : (i + 1) * fields_per]
            if tr_id == "H0STCNT0":
                if len(f) >= 13:
                    await self._parse_price(f)
            elif tr_id == "H0STCNI0":
                await self._parse_execution(f)

    async def _parse_price(self, f: list[str]) -> None:
        """
        H0STCNT0 필드 순서 (주요 필드만):
        [0]  MKSC_SHRN_ISCD  종목코드
        [2]  STCK_PRPR       현재가
        [5]  PRDY_VRSS_SIGN  전일대비부호 (1상한/2상승/3보합/4하한/5하락)
        [6]  PRDY_VRSS       전일대비
        [7]  PRDY_CTRT       전일대비율
        [12] ACML_VOL        누적거래량
        """
        try:
            code  = f[0]
            price = int(f[2])
            sign  = f[5]   # 2=상승, 5=하락, 3=보합
            delta = int(f[6]) if f[6].isdigit() else 0
            pct   = float(f[7]) if f[7] else 0.0
            vol   = int(f[12]) if f[12].isdigit() else 0

            if sign in ("4", "5"):   # 하락/하한
                delta = -delta
                pct   = -abs(pct)

            price_data = {
                "current_price": price,
                "change":        delta,
                "change_pct":    round(pct, 2),
                "volume":        vol,
            }
            self._prices[code] = price_data

            for cb in self._callbacks:
                await cb(code, price_data)
        except Exception as e:
            logger.debug("Price parse error: %s | fields=%s", e, f)

    async def _parse_execution(self, f: list[str]) -> None:
        """
        H0STCNI0 체결통보 파싱.
        [4]  SELN_BYOV_CLS  매도매수구분 (01=매도, 02=매수)
        [7]  STCK_SHRN_ISCD 종목코드
        [8]  CNTG_QTY       체결수량
        [9]  CNTG_UNPR      체결단가
        [12] CNTG_YN        체결여부 (1=체결)
        """
        try:
            if len(f) < 13:
                return
            cntg_yn       = f[12]
            if cntg_yn != "1":   # 체결 아닌 이벤트(접수/확인 등) 무시
                return
            side       = f[4]    # "01"=매도, "02"=매수
            stock_code = f[7]
            fill_qty   = int(f[8]) if f[8].isdigit() else 0
            fill_price = int(f[9]) if f[9].isdigit() else 0

            if fill_qty <= 0 or fill_price <= 0:
                return

            data = {
                "side":        "buy" if side == "02" else "sell",
                "stock_code":  stock_code,
                "fill_qty":    fill_qty,
                "fill_price":  fill_price,
            }
            logger.info("Execution: %s %s x%d @ %d", data["side"], stock_code, fill_qty, fill_price)

            for cb in self._exec_callbacks:
                await cb(data)
        except Exception as e:
            logger.debug("Execution parse error: %s | fields=%s", e, f)


# ------------------------------------------------------------------ #
# 싱글턴
# ------------------------------------------------------------------ #
_client: KISRealtimeClient | None = None


def get_realtime_client() -> KISRealtimeClient | None:
    return _client


def init_realtime_client(app_key: str, app_secret: str, is_real: bool = True) -> KISRealtimeClient:
    global _client
    _client = KISRealtimeClient(app_key, app_secret, is_real)
    return _client

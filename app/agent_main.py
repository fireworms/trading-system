"""
웹 에이전트 서비스 진입점 — trading-backend와 별도 프로세스 (systemd trading-agent, :8001).

분리 이유: 에이전트가 코드를 고친 뒤 `systemctl restart trading-backend`를 하면, 같은
서비스 cgroup에 있는 자식 프로세스(Claude CLI)까지 systemd가 함께 죽인다. 매매 프로세스와
셸 권한을 가진 에이전트를 한 프로세스에 두지 않는다는 격리 의미도 있다.

인증: 같은 SECRET_KEY의 JWT + SUPER_ADMIN만. 토큰은 URL이 아니라 첫 WS 메시지로 받는다
(액세스 로그에 쿼리스트링이 남는다). 접근 경로는 Tailscale 내부망 전제.
"""
import logging
import uuid

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.core.logging import setup_logging

setup_logging()
logger = logging.getLogger(__name__)

from app.api.deps import get_current_user  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.core.security import decode_access_token  # noqa: E402
from app.models.user import User, UserRole  # noqa: E402
from app.services.agent.session import get_hub, list_project_sessions, load_history_events  # noqa: E402

app = FastAPI(title="Trading Agent", version="0.1.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)


def _enabled() -> None:
    if not get_settings().agent_enabled:
        raise HTTPException(status_code=404, detail="agent disabled (AGENT_ENABLED=false)")


def require_super_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="SUPER_ADMIN only")
    return user


def _user_from_token(token: str) -> User | None:
    payload = decode_access_token(token or "")
    if not payload or not payload.get("sub"):
        return None
    with SessionLocal() as db:
        user = db.get(User, uuid.UUID(payload["sub"]))
        if user and user.is_active and user.role == UserRole.SUPER_ADMIN:
            db.expunge(user)
            return user
    return None


@app.get("/api/v1/agent/status")
def status(_: User = Depends(require_super_admin)):
    _enabled()
    hub = get_hub()
    return {"state": hub.state, "session_id": hub.session_id}


@app.get("/api/v1/agent/sessions")
def sessions(_: User = Depends(require_super_admin)):
    _enabled()
    return list_project_sessions(limit=20)


@app.get("/api/v1/agent/sessions/{session_id}/history")
def history(session_id: str, _: User = Depends(require_super_admin)):
    _enabled()
    return load_history_events(session_id)


@app.websocket("/ws/agent")
async def agent_ws(ws: WebSocket):
    await ws.accept()
    if not get_settings().agent_enabled:
        await ws.send_json({"type": "error", "message": "AGENT_ENABLED=false"})
        await ws.close(code=4404)
        return
    try:
        first = await ws.receive_json()
    except Exception:
        await ws.close(code=4400)
        return
    if first.get("type") != "auth" or _user_from_token(first.get("token", "")) is None:
        await ws.send_json({"type": "error", "message": "인증 실패 (SUPER_ADMIN 전용)"})
        await ws.close(code=4401)
        return

    hub = get_hub()
    await hub.attach(ws)
    try:
        while True:
            msg = await ws.receive_json()
            t = msg.get("type")
            if t == "start":
                await hub.start(msg.get("session_id") or None)
            elif t == "message":
                text = str(msg.get("text", "")).strip()
                if text:
                    await hub.send(text)
            elif t == "permission":
                hub.resolve(str(msg.get("id")), bool(msg.get("allow")))
            elif t == "interrupt":
                await hub.interrupt()
            elif t == "close":
                await hub.close()
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning("agent ws error: %s", e)
    finally:
        hub.detach(ws)

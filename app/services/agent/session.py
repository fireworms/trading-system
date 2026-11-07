"""
웹 UI ↔ Claude Code 세션 허브 (claude-agent-sdk).

설계 요점:
- **허브는 WebSocket보다 오래 산다** — 폰 화면이 꺼지면 WS가 끊기지만 진행 중인 턴과
  승인 대기는 그대로 둔다. 다시 붙으면 이벤트 로그 + 대기 중 승인 요청을 재전송.
- **세션은 한 번에 하나** — 같은 세션을 터미널과 웹에서 동시에 이어 쓰면 기록이 꼬인다.
  허브는 단일 클라이언트만 유지하고, 다른 세션을 시작하면 기존 것을 닫는다.
- **권한은 두 겹**: guard.check_tool(PreToolUse 훅, 항상 거부) → can_use_tool(웹 승인 버튼).
- 승인 요청은 10분 무응답이면 거부 (폰을 두고 간 사이 영원히 매달리지 않게).
- 30분 유휴면 CLI 프로세스를 내린다. 세션 기록은 디스크(~/.claude/projects)에 남아 resume 가능.

trading-backend와 **별도 프로세스(trading-agent)** 로 돈다 — 에이전트가 백엔드를 재시작하면
같은 cgroup의 자식(CLI)까지 죽기 때문. 진입점은 app/agent_main.py.
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from app.services.agent.guard import check_tool

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]

_PERMISSION_TIMEOUT_S = 600
_IDLE_DISCONNECT_S = 30 * 60
_LOG_CAP = 400
_PREVIEW_CHARS = 1500

_APPEND_PROMPT = (
    "이 세션은 모바일 웹 UI(trading-agent)를 통해 진행된다. 대화형 터미널이 없다.\n"
    "- AskUserQuestion 도구는 쓸 수 없다. 선택이 필요하면 본문에 번호 목록으로 묻는다.\n"
    "- 화면이 좁다. 답변은 짧게, 표는 3열 이하로.\n"
    "- `sudo systemctl restart trading-agent`는 이 대화를 끊는다 — 필요하면 사용자에게 터미널에서 하라고 안내.\n"
    "- trading-backend 재시작은 이 세션에 영향이 없다 (별도 서비스).\n"
    "- 원칙 4(일상 명령은 사용자가 직접)는 모바일에선 완화한다: 사용자가 승인 버튼으로 실행을 허락하는 구조다."
)


def _cli_path() -> str | None:
    """터미널과 같은 CLI(같은 버전·로그인)를 쓴다. 없으면 SDK 번들 CLI."""
    found = shutil.which("claude") or str(Path.home() / ".local/bin/claude")
    return found if Path(found).exists() else None


def _summarize_input(name: str, inp: dict[str, Any]) -> str:
    if name == "Bash":
        return str(inp.get("command", ""))
    for key in ("file_path", "path", "notebook_path", "pattern", "url", "query", "description"):
        if inp.get(key):
            return f"{key}: {inp[key]}"
    s = json.dumps(inp, ensure_ascii=False)
    return s[:300]


def _preview(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text":
                parts.append(c.get("text", ""))
            elif isinstance(c, dict):
                parts.append(f"[{c.get('type', 'block')}]")
        text = "\n".join(parts)
    else:
        text = str(content)
    return text if len(text) <= _PREVIEW_CHARS else text[:_PREVIEW_CHARS] + f"\n… ({len(text)}자 중 일부)"


def blocks_to_events(role: str, content: Any) -> list[dict[str, Any]]:
    """API 메시지 content → UI 이벤트. 라이브 스트림과 과거 기록 렌더링이 공유."""
    events: list[dict[str, Any]] = []
    if isinstance(content, str):
        if content.strip():
            events.append({"type": "user" if role == "user" else "text", "text": content})
        return events
    for b in content or []:
        if not isinstance(b, dict):
            continue
        t = b.get("type")
        if t == "text" and b.get("text", "").strip():
            events.append({"type": "user" if role == "user" else "text", "text": b["text"]})
        elif t == "tool_use":
            events.append({
                "type": "tool_use", "id": b.get("id"), "name": b.get("name"),
                "summary": _summarize_input(b.get("name", ""), b.get("input") or {}),
            })
        elif t == "tool_result":
            events.append({
                "type": "tool_result", "id": b.get("tool_use_id"),
                "is_error": bool(b.get("is_error")), "preview": _preview(b.get("content")),
            })
    return events


class AgentHub:
    def __init__(self) -> None:
        self._client = None
        self.session_id: str | None = None
        self.state = "disconnected"          # disconnected / idle / running
        self._sockets: set[Any] = set()
        self._log: list[dict[str, Any]] = []
        self._pending: dict[str, dict[str, Any]] = {}   # id → {future, event}
        self._turn_task: asyncio.Task | None = None
        self._reader_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._last_activity = time.monotonic()
        self._reaper: asyncio.Task | None = None

    # ------------------------------------------------------------ 소켓
    async def attach(self, ws) -> None:
        self._sockets.add(ws)
        self._ensure_reaper()
        await self._send(ws, self._status())
        for ev in self._log:
            await self._send(ws, ev)
        for p in self._pending.values():
            await self._send(ws, p["event"])

    def detach(self, ws) -> None:
        self._sockets.discard(ws)

    async def _send(self, ws, ev: dict[str, Any]) -> None:
        try:
            await ws.send_json(ev)
        except Exception:
            self._sockets.discard(ws)

    async def _emit(self, ev: dict[str, Any], *, log: bool = True) -> None:
        if log:
            self._log.append(ev)
            if len(self._log) > _LOG_CAP:
                self._log = self._log[-_LOG_CAP:]
        for ws in list(self._sockets):
            await self._send(ws, ev)

    def _status(self) -> dict[str, Any]:
        return {
            "type": "status", "state": self.state, "session_id": self.session_id,
            "pending": len(self._pending),
        }

    async def _set_state(self, state: str) -> None:
        self.state = state
        await self._emit(self._status(), log=False)

    # ------------------------------------------------------------ 세션 수명
    async def start(self, resume: str | None) -> None:
        """resume=None이면 새 세션. 같은 세션이 이미 붙어 있으면 그대로 둔다."""
        async with self._lock:
            if self._client is not None and resume and resume == self.session_id:
                return
            await self._close_locked()
            from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, HookMatcher

            options = ClaudeAgentOptions(
                cwd=str(PROJECT_ROOT),
                resume=resume,
                permission_mode="default",
                can_use_tool=self._can_use_tool,
                hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[self._guard_hook])]},
                disallowed_tools=["AskUserQuestion"],
                # settings.local.json(터미널에서 "항상 허용"으로 쌓인 규칙)은 웹에서 안 읽는다.
                # `python *`·`.venv/bin/python *` 같은 넓은 규칙이 웹에선 승인 없는 임의 코드
                # 실행이 되고, guard는 Bash 문자열만 보므로 스크립트 안의 .env 읽기를 못 막는다.
                # user/project 설정·CLAUDE.md·메모리는 그대로 (읽기 전용 명령은 CLI가 원래 안 묻는다)
                setting_sources=["user", "project"],
                system_prompt={"type": "preset", "preset": "claude_code", "append": _APPEND_PROMPT},
                cli_path=_cli_path(),
                stderr=lambda line: logger.debug("claude cli: %s", line),
            )
            client = ClaudeSDKClient(options=options)
            await client.connect()
            self._client = client
            self.session_id = resume
            self._log = []
            if resume:
                self._log = load_history_events(resume)
            self._touch()
            await self._set_state("idle")
            for ws in list(self._sockets):     # 히스토리 재동기화
                await self._send(ws, {"type": "reset"})
                for ev in self._log:
                    await self._send(ws, ev)

    async def close(self) -> None:
        async with self._lock:
            await self._close_locked()
        await self._set_state("disconnected")

    async def _close_locked(self) -> None:
        for p in self._pending.values():
            if not p["future"].done():
                p["future"].set_result(False)
        self._pending.clear()
        if self._turn_task and not self._turn_task.done():
            self._turn_task.cancel()
        if self._client is not None:
            try:
                await self._client.disconnect()
            except Exception as e:
                logger.warning("agent disconnect failed: %s", e)
        self._client = None
        self.state = "disconnected"

    # ------------------------------------------------------------ 대화
    async def send(self, text: str) -> None:
        if self._client is None:
            await self.start(self.session_id)
        if self.state == "running":
            await self._emit({"type": "error", "message": "응답 진행 중 — 끝나거나 중단한 뒤 보내세요"}, log=False)
            return
        self._touch()
        await self._emit({"type": "user", "text": text})
        await self._set_state("running")
        self._turn_task = asyncio.create_task(self._run_turn(text))

    async def interrupt(self) -> None:
        if self._client is not None and self.state == "running":
            for p in self._pending.values():
                if not p["future"].done():
                    p["future"].set_result(False)
            try:
                await self._client.interrupt()
            except Exception as e:
                logger.warning("interrupt failed: %s", e)

    async def _run_turn(self, text: str) -> None:
        from claude_agent_sdk import AssistantMessage, ResultMessage, SystemMessage, UserMessage
        try:
            await self._client.query(text)
            async for msg in self._client.receive_response():
                self._touch()
                if isinstance(msg, SystemMessage):
                    if msg.subtype == "init" and msg.data.get("session_id"):
                        self.session_id = msg.data["session_id"]
                        await self._emit(self._status(), log=False)
                elif isinstance(msg, AssistantMessage):
                    if msg.parent_tool_use_id:      # 서브에이전트 내부는 생략
                        continue
                    content = [_block_to_dict(b) for b in msg.content]
                    for ev in blocks_to_events("assistant", content):
                        await self._emit(ev)
                    if msg.error:
                        await self._emit({"type": "error", "message": f"API 오류: {msg.error}"})
                elif isinstance(msg, UserMessage):
                    if msg.parent_tool_use_id or isinstance(msg.content, str):
                        continue
                    content = [_block_to_dict(b) for b in msg.content]
                    for ev in blocks_to_events("user", content):
                        if ev["type"] == "tool_result":
                            await self._emit(ev)
                elif isinstance(msg, ResultMessage):
                    self.session_id = msg.session_id or self.session_id
                    await self._emit({
                        "type": "result", "is_error": msg.is_error,
                        "duration_ms": msg.duration_ms, "cost_usd": msg.total_cost_usd,
                        "terminal_reason": msg.terminal_reason,
                    })
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("agent turn failed")
            await self._emit({"type": "error", "message": f"{type(e).__name__}: {e}"})
            # CLI가 죽었으면 다음 send에서 같은 세션으로 다시 붙는다
            self._client = None
        finally:
            await self._set_state("idle" if self._client is not None else "disconnected")

    # ------------------------------------------------------------ 권한
    async def _guard_hook(self, input_data, tool_use_id, context) -> dict[str, Any]:
        name = input_data.get("tool_name", "")
        reason = check_tool(name, input_data.get("tool_input") or {})
        if reason is None:
            return {}
        await self._emit({"type": "blocked", "name": name, "reason": reason,
                          "summary": _summarize_input(name, input_data.get("tool_input") or {})})
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": f"웹 에이전트 차단 목록: {reason}",
        }}

    async def _can_use_tool(self, tool_name: str, tool_input: dict[str, Any], ctx):
        from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny
        req_id = ctx.tool_use_id or uuid.uuid4().hex
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        event = {
            "type": "permission_request", "id": req_id, "name": tool_name,
            "title": ctx.title or f"{tool_name} 실행",
            "summary": _summarize_input(tool_name, tool_input),
            "reason": ctx.decision_reason,
        }
        self._pending[req_id] = {"future": fut, "event": event}
        await self._emit(event, log=False)
        await self._emit(self._status(), log=False)
        try:
            allowed = await asyncio.wait_for(fut, timeout=_PERMISSION_TIMEOUT_S)
        except asyncio.TimeoutError:
            allowed = False
        finally:
            self._pending.pop(req_id, None)
        self._touch()
        await self._emit({"type": "permission_resolved", "id": req_id, "allowed": allowed,
                          "name": tool_name, "summary": event["summary"]})
        await self._emit(self._status(), log=False)
        if allowed:
            return PermissionResultAllow()
        return PermissionResultDeny(message="사용자가 웹 UI에서 거부했다 (또는 10분 무응답).")

    def resolve(self, req_id: str, allow: bool) -> None:
        p = self._pending.get(req_id)
        if p and not p["future"].done():
            p["future"].set_result(bool(allow))

    # ------------------------------------------------------------ 유휴 정리
    def _touch(self) -> None:
        self._last_activity = time.monotonic()

    def _ensure_reaper(self) -> None:
        if self._reaper is None or self._reaper.done():
            self._reaper = asyncio.create_task(self._reap_loop())

    async def _reap_loop(self) -> None:
        while True:
            await asyncio.sleep(60)
            idle = time.monotonic() - self._last_activity
            if (self._client is not None and self.state == "idle"
                    and not self._pending and idle > _IDLE_DISCONNECT_S):
                logger.info("agent idle %.0fs — disconnecting CLI (session kept on disk)", idle)
                sid = self.session_id
                await self.close()
                self.session_id = sid      # 다음 send가 같은 세션으로 resume


def _block_to_dict(b: Any) -> dict[str, Any]:
    from claude_agent_sdk import TextBlock, ToolResultBlock, ToolUseBlock
    if isinstance(b, TextBlock):
        return {"type": "text", "text": b.text}
    if isinstance(b, ToolUseBlock):
        return {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input}
    if isinstance(b, ToolResultBlock):
        return {"type": "tool_result", "tool_use_id": b.tool_use_id,
                "content": b.content, "is_error": b.is_error}
    return {"type": "other"}


# ---------------------------------------------------------------- 세션 기록
def list_project_sessions(limit: int = 20) -> list[dict[str, Any]]:
    from claude_agent_sdk import list_sessions
    out = []
    for s in list_sessions(directory=str(PROJECT_ROOT), limit=limit, include_worktrees=False):
        out.append({
            "session_id": s.session_id,
            "title": s.custom_title or s.summary or (s.first_prompt or "")[:80],
            "last_modified": s.last_modified,
        })
    return out


def load_history_events(session_id: str, max_events: int = _LOG_CAP) -> list[dict[str, Any]]:
    """디스크 세션 기록 → UI 이벤트 (resume 시 이전 대화 표시용)."""
    from claude_agent_sdk import get_session_messages
    events: list[dict[str, Any]] = []
    try:
        msgs = get_session_messages(session_id, directory=str(PROJECT_ROOT))
    except Exception as e:
        logger.warning("session history load failed: %s", e)
        return events
    for m in msgs:
        message = m.message or {}
        role = message.get("role") or m.type
        evs = blocks_to_events(role, message.get("content"))
        if role == "user":
            # 사용자 쪽은 실제 입력 텍스트 + 도구 결과만. 시스템 리마인더 등 주입 텍스트는 생략
            evs = [e for e in evs if e["type"] == "tool_result"
                   or (e["type"] == "user" and not e["text"].lstrip().startswith("<"))]
        events.extend(evs)
    return events[-max_events:]


_hub: AgentHub | None = None


def get_hub() -> AgentHub:
    global _hub
    if _hub is None:
        _hub = AgentHub()
    return _hub

import { useCallback, useEffect, useRef, useState } from "react";
import { getToken } from "@/lib/api";

// 에이전트는 별도 서비스(trading-agent, :8001) — 백엔드 재시작에 같이 죽지 않게 분리.
// 주소는 접속 호스트 기준 자동 유도 (usePriceStream과 같은 원칙, Tailscale 접속 그대로 동작)
const AGENT_PORT = process.env.NEXT_PUBLIC_AGENT_PORT ?? "8001";
const host = () => (typeof window !== "undefined" ? window.location.hostname : "localhost");
export const AGENT_HTTP = () => `http://${host()}:${AGENT_PORT}/api/v1/agent`;
const AGENT_WS = () => `ws://${host()}:${AGENT_PORT}/ws/agent`;

export type AgentEvent =
  | { type: "user"; text: string }
  | { type: "text"; text: string }
  | { type: "tool_use"; id: string; name: string; summary: string }
  | { type: "tool_result"; id: string; is_error: boolean; preview: string }
  | { type: "blocked"; name: string; reason: string; summary: string }
  | { type: "permission_resolved"; id: string; allowed: boolean; name: string; summary: string }
  | { type: "result"; is_error: boolean; duration_ms: number; cost_usd: number | null; terminal_reason: string | null }
  | { type: "error"; message: string };

export interface PermissionRequest {
  id: string;
  name: string;
  title: string;
  summary: string;
  reason: string | null;
}

export type AgentState = "disconnected" | "idle" | "running";

export function useAgentChat() {
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [pending, setPending] = useState<PermissionRequest[]>([]);
  const [state, setState] = useState<AgentState>("disconnected");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [socketOpen, setSocketOpen] = useState(false);
  const [fatal, setFatal] = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;

    function connect() {
      if (!active) return;
      // 접속마다 서버가 로그 전체를 재전송하므로 로컬 목록을 비운다 (중복 방지)
      setEvents([]);
      setPending([]);
      const ws = new WebSocket(AGENT_WS());
      wsRef.current = ws;
      ws.onopen = () => {
        // 토큰은 URL이 아니라 첫 메시지로 (액세스 로그 보호)
        ws.send(JSON.stringify({ type: "auth", token: getToken() ?? "" }));
        setSocketOpen(true);
      };
      ws.onmessage = (e) => {
        let ev: Record<string, unknown>;
        try { ev = JSON.parse(e.data); } catch { return; }
        switch (ev.type) {
          case "status":
            setState(ev.state as AgentState);
            setSessionId((ev.session_id as string) ?? null);
            if (!ev.pending) setPending([]);
            break;
          case "reset":
            setEvents([]);
            setPending([]);
            break;
          case "permission_request":
            setPending((p) => (p.some((x) => x.id === ev.id) ? p : [...p, ev as unknown as PermissionRequest]));
            break;
          case "permission_resolved":
            setPending((p) => p.filter((x) => x.id !== ev.id));
            setEvents((list) => [...list, ev as unknown as AgentEvent]);
            break;
          default:
            if (ev.type === "error" && typeof ev.message === "string" && ev.message.includes("인증")) {
              setFatal(ev.message);
            }
            setEvents((list) => [...list, ev as unknown as AgentEvent]);
        }
      };
      ws.onclose = (e) => {
        setSocketOpen(false);
        if (e.code === 4401 || e.code === 4404) { active = false; return; }
        if (active) timer = setTimeout(connect, 2000);
      };
      ws.onerror = () => ws.close();
    }

    connect();

    // 폰 화면을 다시 켰을 때 소켓이 죽어 있으면 즉시 재접속
    const onVisible = () => {
      if (!document.hidden && wsRef.current?.readyState === WebSocket.CLOSED && active) {
        clearTimeout(timer);
        connect();
      }
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      active = false;
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
      wsRef.current?.close();
    };
  }, []);

  const send = useCallback((payload: Record<string, unknown>) => {
    const ws = wsRef.current;
    if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify(payload));
  }, []);

  return {
    events, pending, state, sessionId, socketOpen, fatal,
    start: (session_id: string | null) => send({ type: "start", session_id }),
    message: (text: string) => send({ type: "message", text }),
    decide: (id: string, allow: boolean) => {
      setPending((p) => p.filter((x) => x.id !== id));
      send({ type: "permission", id, allow });
    },
    interrupt: () => send({ type: "interrupt" }),
    close: () => send({ type: "close" }),
  };
}

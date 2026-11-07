"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { getToken } from "@/lib/api";
import Markdown from "@/components/Markdown";
import { AGENT_HTTP, AgentEvent, useAgentChat } from "@/hooks/useAgentChat";

interface SessionInfo {
  session_id: string;
  title: string;
  last_modified: number;
}

const STATE_LABEL: Record<string, { text: string; cls: string }> = {
  disconnected: { text: "대기", cls: "bg-gray-700 text-gray-300" },
  idle: { text: "연결됨", cls: "bg-emerald-700/60 text-emerald-200" },
  running: { text: "응답 중", cls: "bg-blue-700/60 text-blue-200 animate-pulse" },
};

function timeAgo(ms: number): string {
  const m = Math.round((Date.now() - ms) / 60000);
  if (m < 1) return "방금";
  if (m < 60) return `${m}분 전`;
  const h = Math.round(m / 60);
  return h < 24 ? `${h}시간 전` : `${Math.round(h / 24)}일 전`;
}

export default function AgentPage() {
  const router = useRouter();
  const chat = useAgentChat();
  const [input, setInput] = useState("");
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [showSessions, setShowSessions] = useState(false);
  const [openTools, setOpenTools] = useState<Record<string, boolean>>({});
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!getToken()) router.push("/login");
  }, [router]);

  function fetchSessions(): Promise<SessionInfo[] | null> {
    return fetch(`${AGENT_HTTP()}/sessions`, {
      headers: { Authorization: `Bearer ${getToken() ?? ""}` },
    })
      .then((res) => (res.ok ? res.json() : null))
      .catch(() => null); // 에이전트 서비스 다운 — 상단 상태로 드러남
  }

  function loadSessions() {
    fetchSessions().then((s) => { if (s) setSessions(s); });
  }

  useEffect(() => {
    fetchSessions().then((s) => { if (s) setSessions(s); });
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chat.events.length, chat.pending.length]);

  // tool_result를 tool_use 아래에 붙여 그리기 위한 맵
  const results = useMemo(() => {
    const m: Record<string, Extract<AgentEvent, { type: "tool_result" }>> = {};
    for (const e of chat.events) if (e.type === "tool_result") m[e.id] = e;
    return m;
  }, [chat.events]);

  function submit() {
    const text = input.trim();
    if (!text || chat.state === "running") return;
    chat.message(text);
    setInput("");
  }

  function pick(id: string | null) {
    chat.start(id);
    setShowSessions(false);
  }

  const badge = STATE_LABEL[chat.state] ?? STATE_LABEL.disconnected;

  if (chat.fatal) {
    return <main className="p-4 text-sm text-red-300">{chat.fatal}</main>;
  }

  return (
    <main className="flex flex-col flex-1 max-w-3xl w-full mx-auto px-3 sm:px-4 pb-3">
      {/* 헤더 */}
      <div className="sticky top-0 z-10 bg-gray-900/95 backdrop-blur py-2 flex items-center gap-2 border-b border-gray-800">
        <span className="font-semibold text-sm">Claude 에이전트</span>
        <span className={`text-[11px] px-1.5 py-0.5 rounded ${badge.cls}`}>
          {chat.socketOpen ? badge.text : "서비스 연결 끊김"}
        </span>
        {chat.sessionId && (
          <span className="text-[11px] text-gray-500 font-mono">{chat.sessionId.slice(0, 8)}</span>
        )}
        <div className="ml-auto flex gap-1.5">
          {chat.state === "running" && (
            <button onClick={chat.interrupt} className="text-xs px-2.5 py-1.5 rounded bg-red-700/70 hover:bg-red-600">
              중단
            </button>
          )}
          <button
            onClick={() => { loadSessions(); setShowSessions((v) => !v); }}
            className="text-xs px-2.5 py-1.5 rounded bg-gray-700 hover:bg-gray-600"
          >
            세션
          </button>
        </div>
      </div>

      {/* 세션 선택 */}
      {(showSessions || (chat.state === "disconnected" && chat.events.length === 0)) && (
        <div className="mt-3 rounded-lg border border-gray-700 bg-gray-800/60 p-3 space-y-2">
          <p className="text-[11px] text-amber-300/90">
            터미널에서 쓰고 있는 세션을 여기서 동시에 이어 쓰지 마세요 — 기록이 꼬입니다. 한쪽만 쓰세요.
          </p>
          <button
            onClick={() => pick(null)}
            className="w-full text-left text-sm px-3 py-2 rounded bg-blue-700/70 hover:bg-blue-600"
          >
            + 새 세션
          </button>
          {sessions.map((s) => (
            <button
              key={s.session_id}
              onClick={() => pick(s.session_id)}
              className={`w-full text-left px-3 py-2 rounded hover:bg-gray-700 ${
                s.session_id === chat.sessionId ? "bg-gray-700" : "bg-gray-900/60"
              }`}
            >
              <div className="text-sm truncate">{s.title || "(제목 없음)"}</div>
              <div className="text-[11px] text-gray-500">
                {timeAgo(s.last_modified)} · <span className="font-mono">{s.session_id.slice(0, 8)}</span>
              </div>
            </button>
          ))}
        </div>
      )}

      {/* 대화 */}
      <div className="flex-1 py-3 space-y-2.5">
        {chat.events.map((e, i) => {
          switch (e.type) {
            case "user":
              return (
                <div key={i} className="flex justify-end">
                  <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-blue-700/70 px-3 py-2 text-sm whitespace-pre-wrap break-words">
                    {e.text}
                  </div>
                </div>
              );
            case "text":
              return (
                <div key={i} className="text-sm leading-relaxed break-words">
                  <Markdown text={e.text} />
                </div>
              );
            case "tool_use": {
              const r = results[e.id];
              const open = openTools[e.id];
              return (
                <div key={i} className="rounded border border-gray-800 bg-gray-950/60">
                  <button
                    onClick={() => setOpenTools((o) => ({ ...o, [e.id]: !o[e.id] }))}
                    className="w-full text-left px-2.5 py-1.5 flex items-center gap-2"
                  >
                    <span className={`text-[10px] px-1 rounded ${r?.is_error ? "bg-red-900 text-red-200" : "bg-gray-800 text-gray-400"}`}>
                      {e.name}
                    </span>
                    <span className="text-xs font-mono text-gray-300 truncate flex-1">{e.summary}</span>
                    <span className="text-[10px] text-gray-500">{r ? (open ? "▲" : "▼") : "…"}</span>
                  </button>
                  {open && r && (
                    <pre className="px-2.5 pb-2 text-[11px] text-gray-400 whitespace-pre-wrap break-all max-h-72 overflow-y-auto">
                      {r.preview || "(출력 없음)"}
                    </pre>
                  )}
                </div>
              );
            }
            case "tool_result":
              return null; // tool_use 아래에 접어서 표시
            case "blocked":
              return (
                <div key={i} className="text-xs rounded border border-red-900 bg-red-950/40 px-2.5 py-1.5 text-red-300">
                  ⛔ 차단: {e.reason}
                  <div className="font-mono text-red-400/80 truncate">{e.summary}</div>
                </div>
              );
            case "permission_resolved":
              return (
                <div key={i} className={`text-[11px] ${e.allowed ? "text-emerald-400/80" : "text-gray-500"}`}>
                  {e.allowed ? "✓ 허용" : "✕ 거부"} · {e.name}
                </div>
              );
            case "result":
              return (
                <div key={i} className="text-[10px] text-gray-600 text-right">
                  {(e.duration_ms / 1000).toFixed(1)}s
                  {e.terminal_reason && e.terminal_reason !== "completed" ? ` · ${e.terminal_reason}` : ""}
                </div>
              );
            case "error":
              return (
                <div key={i} className="text-xs text-red-400 whitespace-pre-wrap">⚠ {e.message}</div>
              );
            default:
              return null;
          }
        })}
        <div ref={bottomRef} />
      </div>

      {/* 승인 대기 */}
      {chat.pending.length > 0 && (
        <div className="sticky bottom-[88px] z-10 space-y-2 mb-2">
          {chat.pending.map((p) => (
            <div key={p.id} className="rounded-lg border border-amber-600/70 bg-gray-900 shadow-lg p-3">
              <div className="text-xs text-amber-300 mb-1">승인 필요 · {p.title}</div>
              <pre className="text-[11px] font-mono text-gray-200 bg-gray-950 rounded p-2 whitespace-pre-wrap break-all max-h-40 overflow-y-auto">
                {p.summary}
              </pre>
              {p.reason && <div className="text-[11px] text-gray-500 mt-1">{p.reason}</div>}
              <div className="flex gap-2 mt-2">
                <button
                  onClick={() => chat.decide(p.id, false)}
                  className="flex-1 py-2.5 rounded bg-gray-700 hover:bg-gray-600 text-sm"
                >
                  거부
                </button>
                <button
                  onClick={() => chat.decide(p.id, true)}
                  className="flex-1 py-2.5 rounded bg-amber-600 hover:bg-amber-500 text-sm font-medium"
                >
                  허용
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* 입력 */}
      <div className="sticky bottom-0 bg-gray-900 pt-2 flex gap-2 items-end">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); submit(); }
          }}
          rows={2}
          placeholder={chat.state === "running" ? "응답 중…" : "메시지 (Ctrl+Enter 전송)"}
          className="flex-1 resize-none rounded-lg bg-gray-800 border border-gray-700 px-3 py-2 text-sm focus:outline-none focus:border-blue-600"
        />
        <button
          onClick={submit}
          disabled={!input.trim() || chat.state === "running" || !chat.socketOpen}
          className="px-4 py-2.5 rounded-lg bg-blue-600 hover:bg-blue-500 disabled:opacity-40 text-sm font-medium"
        >
          전송
        </button>
      </div>
    </main>
  );
}

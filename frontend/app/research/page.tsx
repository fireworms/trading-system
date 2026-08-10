"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  api, getToken,
  ResearchCandidate, ResearchNoteDetail, ResearchNoteSummary,
} from "@/lib/api";
import StockSearch from "@/components/StockSearch";
import Markdown from "@/components/Markdown";

interface PinnedStock {
  code: string;
  name: string;
}

export default function ResearchPage() {
  const router = useRouter();
  const [question, setQuestion] = useState("");
  const [pinned, setPinned] = useState<PinnedStock | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [candidates, setCandidates] = useState<ResearchCandidate[] | null>(null);
  const [note, setNote] = useState<ResearchNoteDetail | null>(null);
  const [history, setHistory] = useState<ResearchNoteSummary[]>([]);

  useEffect(() => {
    if (!getToken()) { router.push("/login"); return; }
    api.research.history().then(setHistory).catch(() => {});
  }, [router]);

  async function submit(stockCode?: string) {
    const q = question.trim();
    if (!q || loading) return;
    setLoading(true);
    setError(null);
    setNotice(null);
    setCandidates(null);
    try {
      const res = await api.research.query({
        question: q,
        ...(stockCode ? { stock_code: stockCode } : {}),
      });
      if (res.status === "done" && res.note) {
        setNote(res.note);
        setHistory(await api.research.history().catch(() => history));
      } else if (res.status === "ambiguous") {
        setCandidates(res.candidates ?? []);
        setNotice(res.message ?? "분석할 종목을 선택해주세요.");
      } else {
        setNotice(res.message ?? "종목을 인식하지 못했습니다.");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "리서치 실패");
    } finally {
      setLoading(false);
    }
  }

  async function openHistory(id: string) {
    setError(null);
    try {
      setNote(await api.research.detail(id));
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (e) {
      setError(e instanceof Error ? e.message : "조회 실패");
    }
  }

  async function removeHistory(id: string) {
    if (!confirm("이 리서치 기록을 삭제할까요?")) return;
    try {
      await api.research.remove(id);
      setHistory((h) => h.filter((n) => n.research_id !== id));
      if (note?.research_id === id) setNote(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "삭제 실패");
    }
  }

  return (
    <div className="max-w-6xl mx-auto px-6 py-6 flex flex-col gap-5">
      <div>
        <h1 className="text-xl font-bold text-white">AI 리서치</h1>
        <p className="text-sm text-gray-400 mt-1">
          자유 질문으로 종목 리서치 — KIS 실측 데이터 + DART 공시 + 뉴스 + 검색 그라운딩 기반
        </p>
      </div>

      {/* 참고용 프레이밍 — 이 탭 결과는 매매 시그널이 아님 */}
      <div className="bg-amber-900/30 border border-amber-700/50 rounded-lg px-4 py-3 text-xs text-amber-200/90 leading-relaxed">
        ⚠️ 이 결과는 <b>리서치 참고 자료</b>이며 매매 시그널이 아닙니다. AI 서술형 전망의 예측력은
        검증되지 않았습니다 — 인용된 수치는 KIS/DART 실측이지만, 해석과 시나리오는 반드시 직접 검증하세요.
      </div>

      {/* 질문 입력 */}
      <div className="bg-gray-800 rounded-xl p-4 flex flex-col gap-3">
        <textarea
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          rows={3}
          placeholder='예: "삼성전자 HBM 경쟁력이 하이닉스를 따라잡을 수 있는지, 최근 수급이랑 같이 봐줘"'
          className="w-full bg-gray-700 text-white rounded-lg px-4 py-3 text-sm outline-none focus:ring-2 focus:ring-blue-500 resize-none"
        />
        <div className="flex flex-wrap items-center gap-3">
          <div className="w-64">
            <StockSearch
              placeholder="종목 직접 지정 (선택)"
              onSelect={(s) => setPinned({ code: s.code, name: s.name })}
            />
          </div>
          {pinned && (
            <span className="flex items-center gap-1.5 bg-blue-900/50 border border-blue-700/50 text-blue-200 text-xs px-2.5 py-1 rounded-full">
              {pinned.name} <span className="font-mono text-blue-400">{pinned.code}</span>
              <button onClick={() => setPinned(null)} className="ml-1 text-blue-300 hover:text-white">✕</button>
            </span>
          )}
          <button
            onClick={() => submit(pinned?.code)}
            disabled={loading || !question.trim()}
            className="ml-auto bg-blue-600 hover:bg-blue-500 disabled:opacity-40 disabled:cursor-not-allowed text-white text-sm font-medium px-5 py-2 rounded-lg"
          >
            {loading ? "분석 중..." : "리서치 실행"}
          </button>
        </div>
        {loading && (
          <p className="text-xs text-gray-400">
            KIS 데이터 수집 + AI 검색 분석 중 — 1~2분 걸릴 수 있어요. 페이지를 벗어나지 마세요.
          </p>
        )}
        {error && <p className="text-sm text-red-400">{error}</p>}
        {notice && !candidates && <p className="text-sm text-amber-300">{notice}</p>}

        {/* 종목 후보 선택 (ambiguous) */}
        {candidates && candidates.length > 0 && (
          <div className="flex flex-col gap-2">
            <p className="text-sm text-amber-300">{notice}</p>
            <div className="flex flex-wrap gap-2">
              {candidates.map((c) => (
                <button
                  key={c.stock_code}
                  onClick={() => submit(c.stock_code)}
                  disabled={loading}
                  className="bg-gray-700 hover:bg-blue-600 text-sm text-white px-3 py-1.5 rounded-lg flex items-center gap-2"
                >
                  {c.stock_name}
                  <span className="text-xs font-mono text-gray-400">{c.stock_code}</span>
                  <span className={`text-xs ${c.market === "KOSPI" ? "text-blue-400" : "text-green-400"}`}>
                    {c.market}
                  </span>
                </button>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* 결과 */}
      {note && (
        <div className="bg-gray-800 rounded-xl p-5 flex flex-col gap-3">
          <div className="flex items-start justify-between gap-3 border-b border-gray-700 pb-3">
            <div>
              <div className="flex items-center gap-2">
                <span className="text-base font-bold text-white">{note.stock_name}</span>
                <span className="text-xs font-mono text-gray-400">{note.stock_code}</span>
              </div>
              <p className="text-xs text-gray-500 mt-1">Q. {note.question}</p>
            </div>
            <div className="text-right text-xs text-gray-500 shrink-0">
              <div>{new Date(note.created_at).toLocaleString("ko-KR")}</div>
              <div className="mt-0.5 font-mono">{note.gemini_model}</div>
            </div>
          </div>
          <Markdown text={note.answer_md} />
          <p className="text-xs text-gray-600 border-t border-gray-700 pt-3">
            참고용 리서치 — 매매 판단은 직접. 링크·수치는 유통기한이 있으니 원문을 확인하세요.
          </p>
        </div>
      )}

      {/* 이력 */}
      <div className="bg-gray-800 rounded-xl p-4">
        <h2 className="text-sm font-semibold text-gray-300 mb-3">리서치 이력</h2>
        {history.length === 0 ? (
          <p className="text-sm text-gray-500">아직 리서치 기록이 없습니다.</p>
        ) : (
          <ul className="flex flex-col divide-y divide-gray-700/60">
            {history.map((h) => (
              <li key={h.research_id} className="flex items-center gap-3 py-2.5">
                <button
                  onClick={() => openHistory(h.research_id)}
                  className="flex-1 text-left group"
                >
                  <span className="text-sm text-white group-hover:text-blue-400">
                    {h.stock_name}
                    <span className="ml-1.5 text-xs font-mono text-gray-500">{h.stock_code}</span>
                  </span>
                  <span className="block text-xs text-gray-400 truncate mt-0.5">{h.question}</span>
                </button>
                <span className="text-xs text-gray-500 shrink-0">
                  {new Date(h.created_at).toLocaleDateString("ko-KR")}
                </span>
                <button
                  onClick={() => removeHistory(h.research_id)}
                  className="text-xs text-gray-600 hover:text-red-400 shrink-0"
                  title="삭제"
                >
                  ✕
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

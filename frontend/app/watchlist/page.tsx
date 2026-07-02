"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  api, getToken,
  WatchlistItem, StockAnalysisSummary, StockAnalysisDetail, WatchTriggerType,
} from "@/lib/api";
import StockSearch from "@/components/StockSearch";

const TRIGGER_LABEL: Record<string, string> = {
  manual:      "수동",
  earnings:    "실적발표",
  disclosure:  "공시",
  flow_spike:  "수급 급변",
  price_spike: "주가 급변",
};

function fmtNum(v: unknown, digits = 1): string {
  if (typeof v !== "number") return "-";
  return v.toLocaleString("ko-KR", { maximumFractionDigits: digits });
}

function PctCell({ v }: { v: unknown }) {
  if (typeof v !== "number") return <span className="text-gray-600">-</span>;
  return (
    <span className={v >= 0 ? "text-red-400" : "text-blue-400"}>
      {v >= 0 ? "+" : ""}{v.toFixed(2)}%
    </span>
  );
}

/** 억원 단위 금액 → 조/억 표기 */
function fmtEok(v: unknown): string {
  if (typeof v !== "number") return "-";
  if (Math.abs(v) >= 10000) return `${(v / 10000).toFixed(1)}조`;
  return `${v.toLocaleString("ko-KR", { maximumFractionDigits: 0 })}억`;
}

/** 백만원 단위 금액 → 조/억 표기 */
function fmtMillion(v: unknown): string {
  if (typeof v !== "number") return "-";
  const eok = v / 100;
  return fmtEok(eok);
}

// ------------------------------------------------------------------ //
// 분석 상세 뷰: 5개 섹션 + 입력 스냅샷 (사후 검증용으로 함께 표시)
// ------------------------------------------------------------------ //
function AnalysisDetailView({ detail }: { detail: StockAnalysisDetail }) {
  const r = detail.result ?? {};
  const snap = detail.input_snapshot ?? {};
  const price = snap.price ?? {};
  const val = snap.valuation_current ?? {};
  const flow = snap.investor_flow ?? {};
  const quarters: Record<string, unknown>[] = snap.fundamentals_quarterly?.income_single_q ?? [];
  const flags: Record<string, string> = snap.data_flags ?? {};
  const sources = r["뉴스_출처"] ?? [];

  return (
    <div className="flex flex-col gap-4">
      {/* AI 구조화 출력 5개 섹션 */}
      <Section title="논거" tone="border-gray-600">
        <p className="text-sm text-gray-200 leading-relaxed whitespace-pre-wrap">{r["논거"] || "-"}</p>
      </Section>

      <Section title="단기 촉매" tone="border-gray-600">
        {(r["단기_촉매"] ?? []).length === 0 ? (
          <p className="text-sm text-gray-500">없음</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {(r["단기_촉매"] ?? []).map((c, i) => (
              <li key={i} className="text-sm text-gray-200 flex items-start gap-2">
                <span className={`shrink-0 text-xs px-1.5 py-0.5 rounded mt-0.5 ${
                  c["성격"] === "구조적" ? "bg-purple-900/60 text-purple-300" : "bg-gray-700 text-gray-400"
                }`}>{c["성격"] || "?"}</span>
                <span>{c["이벤트"]}{c["예상_시점"] ? ` — ${c["예상_시점"]}` : ""}</span>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="장기 논거" tone="border-gray-600">
        <p className="text-sm text-gray-200 leading-relaxed whitespace-pre-wrap">{r["장기_논거"] || "-"}</p>
      </Section>

      <Section title="무효화 조건 — 이 신호가 뜨면 이 판단은 틀린 것" tone="border-amber-700/60">
        <ul className="flex flex-col gap-1.5 list-disc list-inside">
          {(r["무효화_조건"] ?? []).map((c, i) => (
            <li key={i} className="text-sm text-amber-200/90 leading-relaxed">{c}</li>
          ))}
        </ul>
      </Section>

      <Section title="밸류 코멘트" tone="border-gray-600">
        <p className="text-sm text-gray-200 leading-relaxed whitespace-pre-wrap">{r["밸류_코멘트"] || "-"}</p>
      </Section>

      {/* 입력 스냅샷 — 그날 AI가 실제로 본 데이터 */}
      <div className="border-t border-gray-700 pt-4 flex flex-col gap-4">
        <h3 className="text-sm font-semibold text-gray-400">
          입력 스냅샷 <span className="font-normal text-gray-500">
            (수집: {snap.collected_at ? new Date(snap.collected_at).toLocaleString("ko-KR") : "-"})
          </span>
        </h3>

        {/* 가격/밸류 요약 */}
        <div className="grid grid-cols-3 sm:grid-cols-6 gap-3 text-xs">
          <SnapCell label="현재가" value={fmtNum(price.current_price, 0)} />
          <SnapCell label="1개월" value={<PctCell v={price.return_1m_pct} />} />
          <SnapCell label="3개월" value={<PctCell v={price.return_3m_pct} />} />
          <SnapCell label="6개월" value={<PctCell v={price.return_6m_pct} />} />
          <SnapCell label="6M 밴드 위치" value={typeof price.pos_in_6m_band_pct === "number" ? `${price.pos_in_6m_band_pct}%` : "-"} />
          <SnapCell label="RSI(14)" value={fmtNum(price.rsi_14)} />
          <SnapCell label="PER(직전실적)" value={fmtNum(val.per_trailing, 2)} />
          <SnapCell label="PBR" value={fmtNum(val.pbr, 2)} />
          <SnapCell label="외인 소진율" value={typeof flow.frgn_exhaust_rate_pct === "number" ? `${flow.frgn_exhaust_rate_pct}%` : "-"} />
          <SnapCell label="외인 5일" value={fmtMillion(flow.frgn_net_5d)} />
          <SnapCell label="외인 30일" value={fmtMillion(flow.frgn_net_30d)} />
          <SnapCell label="기관 30일" value={fmtMillion(flow.orgn_net_30d)} />
        </div>

        {/* 분기 실적 (단일분기 차분) */}
        {quarters.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-gray-500 border-b border-gray-700">
                  <th className="text-left py-1.5 pr-2">분기</th>
                  <th className="text-right py-1.5 px-2">매출</th>
                  <th className="text-right py-1.5 px-2">영업이익</th>
                  <th className="text-right py-1.5 px-2">영업이익률</th>
                  <th className="text-right py-1.5 px-2">매출 YoY</th>
                  <th className="text-right py-1.5 pl-2">영업익 YoY</th>
                </tr>
              </thead>
              <tbody>
                {quarters.slice(0, 6).map((q, i) => (
                  <tr key={i} className="border-b border-gray-800 text-gray-300">
                    <td className="py-1.5 pr-2 font-mono">{String(q.period ?? "-")}</td>
                    <td className="text-right py-1.5 px-2">{fmtEok(q.revenue_q)}</td>
                    <td className="text-right py-1.5 px-2">{fmtEok(q.operating_profit_q)}</td>
                    <td className="text-right py-1.5 px-2">{typeof q.op_margin_q_pct === "number" ? `${q.op_margin_q_pct}%` : "-"}</td>
                    <td className="text-right py-1.5 px-2"><PctCell v={q.revenue_yoy_pct} /></td>
                    <td className="text-right py-1.5 pl-2"><PctCell v={q.op_yoy_pct} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* 뉴스 출처 */}
        {sources.length > 0 && (
          <div>
            <p className="text-xs text-gray-500 mb-1.5">사용된 뉴스/공시 출처</p>
            <ul className="flex flex-col gap-1">
              {sources.map((s, i) => (
                <li key={i} className="text-xs text-gray-400">
                  {s.url ? (
                    <a href={s.url} target="_blank" rel="noreferrer" className="text-blue-400 hover:underline">
                      {s["제목"] || s.url}
                    </a>
                  ) : (
                    <span className="text-gray-300">{s["제목"]}</span>
                  )}
                  <span className="ml-1.5 text-gray-600">{s["매체"]}{s["날짜"] ? ` · ${s["날짜"]}` : ""}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* 데이터 결측 플래그 — 뭘 못 보고 판단했는지 기록 */}
        {Object.keys(flags).length > 0 && (
          <div>
            <p className="text-xs text-gray-500 mb-1.5">데이터 공백 (분석에 반영 안 된 것)</p>
            <ul className="flex flex-col gap-0.5">
              {Object.entries(flags).map(([k, v]) => (
                <li key={k} className="text-xs text-gray-600">• {v}</li>
              ))}
            </ul>
          </div>
        )}

        <details className="text-xs">
          <summary className="text-gray-500 cursor-pointer hover:text-gray-300">전체 스냅샷 원본 (JSON)</summary>
          <pre className="mt-2 bg-gray-950 rounded-lg p-3 overflow-x-auto text-gray-400 max-h-96 overflow-y-auto">
            {JSON.stringify(snap, null, 2)}
          </pre>
        </details>
      </div>
    </div>
  );
}

function Section({ title, tone, children }: { title: string; tone: string; children: React.ReactNode }) {
  return (
    <div className={`border-l-2 ${tone} pl-3`}>
      <h3 className="text-xs font-semibold text-gray-400 mb-1.5">{title}</h3>
      {children}
    </div>
  );
}

function SnapCell({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="bg-gray-900/60 rounded-lg px-2.5 py-2">
      <p className="text-gray-500 mb-0.5">{label}</p>
      <p className="text-gray-200">{value}</p>
    </div>
  );
}

// ------------------------------------------------------------------ //
// 메인 페이지
// ------------------------------------------------------------------ //
export default function WatchlistPage() {
  const router = useRouter();
  const [items, setItems] = useState<WatchlistItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<WatchlistItem | null>(null);
  const [analyses, setAnalyses] = useState<StockAnalysisSummary[]>([]);
  const [detail, setDetail] = useState<StockAnalysisDetail | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [memoEdit, setMemoEdit] = useState<string | null>(null);
  const [analysisDate, setAnalysisDate] = useState<string>("");
  const [triggerType, setTriggerType] = useState<WatchTriggerType>("manual");

  useEffect(() => {
    if (!getToken()) { router.push("/login"); return; }
    reload();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function reload() {
    try {
      const list = await api.watchlist.list();
      setItems(list);
    } finally {
      setLoading(false);
    }
  }

  async function select(item: WatchlistItem) {
    setSelected(item);
    setDetail(null);
    setMemoEdit(null);
    setError(null);
    const list = await api.watchlist.analyses(item.stock_code);
    setAnalyses(list);
    if (list.length > 0) {
      setDetail(await api.watchlist.analysisDetail(list[0].analysis_id));
    }
  }

  async function addStock(code: string, country?: string | null) {
    setError(null);
    if (country === "US") {
      setError("관심종목 분석은 국내(KOSPI/KOSDAQ) 종목만 지원합니다 (KIS 재무 API 제약).");
      return;
    }
    try {
      await api.watchlist.add(code);
      await reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "추가 실패");
    }
  }

  async function removeStock(item: WatchlistItem) {
    if (!confirm(`${item.stock_name}을(를) 관심종목에서 제거할까요?\n(분석 일지는 보존됩니다)`)) return;
    await api.watchlist.remove(item.watch_id);
    if (selected?.watch_id === item.watch_id) {
      setSelected(null);
      setAnalyses([]);
      setDetail(null);
    }
    await reload();
  }

  async function saveMemo(item: WatchlistItem, memo: string) {
    const updated = await api.watchlist.updateMemo(item.watch_id, memo);
    setItems((prev) => prev.map((w) => (w.watch_id === updated.watch_id ? updated : w)));
    if (selected?.watch_id === updated.watch_id) setSelected(updated);
    setMemoEdit(null);
  }

  async function runAnalysis() {
    if (!selected || analyzing) return;
    setAnalyzing(true);
    setError(null);
    try {
      const result = await api.watchlist.analyze({
        stock_code: selected.stock_code,
        ...(analysisDate ? { analysis_date: analysisDate } : {}),
        trigger_type: triggerType,
      });
      setDetail(result);
      setAnalyses(await api.watchlist.analyses(selected.stock_code));
      await reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "분석 실패");
    } finally {
      setAnalyzing(false);
    }
  }

  if (loading) return (
    <div className="min-h-screen bg-gray-900 flex items-center justify-center text-gray-400">로딩 중...</div>
  );

  return (
    <div className="min-h-screen bg-gray-900 text-white p-6">
      <div className="max-w-7xl mx-auto flex flex-col gap-4">
        <div className="flex items-center justify-between">
          <h1 className="text-xl font-bold">관심종목 분석 <span className="text-sm font-normal text-gray-500">중장기 수동매매 일지</span></h1>
          <span className="text-sm text-gray-400">{items.length}종목</span>
        </div>

        {error && (
          <div className="bg-red-900/40 border border-red-700 text-red-300 text-sm rounded-lg px-4 py-2.5">{error}</div>
        )}

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          {/* 좌: 관심종목 리스트 */}
          <div className="flex flex-col gap-3">
            <StockSearch
              onSelect={(s) => addStock(s.code, s.country)}
              placeholder="관심종목 추가 (국내 종목)"
            />
            {items.length === 0 ? (
              <p className="text-gray-500 text-sm py-4">관심종목이 없습니다. 위에서 검색해 추가하세요.</p>
            ) : (
              <div className="flex flex-col gap-2">
                {items.map((w) => (
                  <div
                    key={w.watch_id}
                    onClick={() => select(w)}
                    className={`bg-gray-800 rounded-xl p-3.5 cursor-pointer border transition-colors ${
                      selected?.watch_id === w.watch_id
                        ? "border-blue-600"
                        : "border-transparent hover:border-gray-600"
                    }`}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div>
                        <span className="font-medium text-sm">{w.stock_name}</span>
                        <span className="ml-2 text-xs text-gray-500 font-mono">{w.stock_code}</span>
                        {w.sector && <p className="text-xs text-gray-500 mt-0.5">{w.sector}</p>}
                      </div>
                      <button
                        onClick={(e) => { e.stopPropagation(); removeStock(w); }}
                        className="text-xs text-gray-600 hover:text-red-400 shrink-0"
                      >
                        삭제
                      </button>
                    </div>

                    {/* 메모 */}
                    {memoEdit !== null && selected?.watch_id === w.watch_id ? (
                      <div className="mt-2 flex gap-1.5" onClick={(e) => e.stopPropagation()}>
                        <input
                          value={memoEdit}
                          onChange={(e) => setMemoEdit(e.target.value)}
                          onKeyDown={(e) => e.key === "Enter" && saveMemo(w, memoEdit)}
                          className="flex-1 bg-gray-700 rounded px-2 py-1 text-xs outline-none focus:ring-1 focus:ring-blue-500"
                          placeholder="메모"
                          autoFocus
                        />
                        <button onClick={() => saveMemo(w, memoEdit)} className="text-xs text-blue-400">저장</button>
                      </div>
                    ) : (
                      <p
                        className="mt-1.5 text-xs text-gray-400 hover:text-gray-200"
                        onClick={(e) => { e.stopPropagation(); setSelected(w); setMemoEdit(w.memo); }}
                      >
                        {w.memo || <span className="text-gray-600 italic">메모 추가...</span>}
                      </p>
                    )}

                    <div className="mt-2 flex items-center gap-2 text-xs text-gray-500">
                      <span>분석 {w.analysis_count}회</span>
                      {w.last_analysis_date && <span>· 최근 {w.last_analysis_date}</span>}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* 우: 분석 이력 + 상세 */}
          <div className="lg:col-span-2 flex flex-col gap-3">
            {!selected ? (
              <div className="bg-gray-800 rounded-xl p-8 text-center text-gray-500 text-sm">
                종목을 선택하면 분석 일지가 표시됩니다.
              </div>
            ) : (
              <>
                {/* 분석 트리거 바 */}
                <div className="bg-gray-800 rounded-xl p-4 flex flex-wrap items-center gap-3">
                  <span className="font-semibold text-sm">{selected.stock_name}</span>
                  <select
                    value={triggerType}
                    onChange={(e) => setTriggerType(e.target.value as WatchTriggerType)}
                    className="bg-gray-700 rounded-lg px-2.5 py-1.5 text-xs outline-none"
                  >
                    {Object.entries(TRIGGER_LABEL).map(([k, v]) => (
                      <option key={k} value={k}>{v}</option>
                    ))}
                  </select>
                  <input
                    type="date"
                    value={analysisDate}
                    onChange={(e) => setAnalysisDate(e.target.value)}
                    className="bg-gray-700 rounded-lg px-2.5 py-1 text-xs outline-none"
                    title="분석 기준일 (라벨용 — 입력 데이터는 항상 수집 시점 기준)"
                  />
                  <button
                    onClick={runAnalysis}
                    disabled={analyzing}
                    className={`px-4 py-1.5 rounded-lg text-sm font-medium transition-colors ${
                      analyzing
                        ? "bg-gray-700 text-gray-500 cursor-not-allowed"
                        : "bg-blue-600 hover:bg-blue-500 text-white"
                    }`}
                  >
                    {analyzing ? "분석 중... (30초~1분)" : "분석 실행"}
                  </button>
                  <span className="text-xs text-gray-600">
                    AI는 데이터 정리만 — 매수/매도 판단은 직접
                  </span>
                </div>

                {/* 일자별 이력 타임라인 */}
                {analyses.length > 0 && (
                  <div className="flex gap-2 overflow-x-auto pb-1">
                    {analyses.map((a) => (
                      <button
                        key={a.analysis_id}
                        onClick={async () => setDetail(await api.watchlist.analysisDetail(a.analysis_id))}
                        className={`shrink-0 px-3 py-1.5 rounded-lg text-xs transition-colors ${
                          detail?.analysis_id === a.analysis_id
                            ? "bg-blue-600 text-white"
                            : "bg-gray-800 text-gray-400 hover:bg-gray-700"
                        }`}
                      >
                        {a.analysis_date}
                        <span className="ml-1.5 opacity-60">{TRIGGER_LABEL[a.trigger_type] ?? a.trigger_type}</span>
                      </button>
                    ))}
                  </div>
                )}

                {/* 상세 */}
                {detail ? (
                  <div className="bg-gray-800 rounded-xl p-5">
                    <div className="flex items-center justify-between mb-4">
                      <h2 className="text-sm font-semibold">
                        {detail.analysis_date} 분석
                        <span className="ml-2 text-xs font-normal text-gray-500">
                          {TRIGGER_LABEL[detail.trigger_type] ?? detail.trigger_type} · {detail.gemini_model}
                        </span>
                      </h2>
                      <span className="text-xs text-gray-600">
                        {new Date(detail.created_at).toLocaleString("ko-KR")}
                      </span>
                    </div>
                    <AnalysisDetailView detail={detail} />
                  </div>
                ) : (
                  <div className="bg-gray-800 rounded-xl p-8 text-center text-gray-500 text-sm">
                    아직 분석 이력이 없습니다. 위에서 분석을 실행하세요.
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

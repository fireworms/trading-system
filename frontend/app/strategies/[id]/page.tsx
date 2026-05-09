"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import {
  api, RecommendationRun, StrategyStats, BacktestResult, BacktestRunSummary, getToken,
} from "@/lib/api";
import RecommendationTable from "@/components/RecommendationTable";
import WinRateChart from "@/components/WinRateChart";
import StatCard from "@/components/StatCard";

type Tab = "live" | "backtest";

export default function StrategyDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router  = useRouter();

  const [tab, setTab] = useState<Tab>("live");

  // ── 라이브 탭 상태 ──────────────────────────────────────
  const [runs, setRuns]     = useState<RecommendationRun[]>([]);
  const [stats, setStats]   = useState<StrategyStats | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [loading, setLoading]   = useState(true);

  // ── 백테스트 탭 상태 ────────────────────────────────────
  const [btDate, setBtDate]           = useState("");
  const [btRunning, setBtRunning]     = useState(false);
  const [btResult, setBtResult]       = useState<BacktestResult | null>(null);
  const [btHistory, setBtHistory]     = useState<BacktestRunSummary[]>([]);
  const [btError, setBtError]         = useState("");
  const [btHistoryLoading, setBtHistoryLoading] = useState(false);

  useEffect(() => {
    if (!getToken()) { router.push("/login"); return; }
    loadLive();
  }, [id]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (tab === "backtest") loadBtHistory();
  }, [tab]); // eslint-disable-line react-hooks/exhaustive-deps

  async function loadLive() {
    try {
      const [runsData, statsData] = await Promise.all([
        api.recommendations.runs(id),
        api.recommendations.stats(id).catch(() => null),
      ]);
      setRuns(runsData);
      setStats(statsData);
      if (runsData.length > 0) setSelected(runsData[0].run_id);
    } finally {
      setLoading(false);
    }
  }

  async function loadBtHistory() {
    setBtHistoryLoading(true);
    try {
      const data = await api.admin.getBacktestResults(id);
      setBtHistory(data);
    } catch {
      /* 권한 없으면 조용히 */
    } finally {
      setBtHistoryLoading(false);
    }
  }

  async function runBacktest() {
    if (!btDate) { setBtError("날짜를 선택하세요"); return; }
    setBtRunning(true);
    setBtResult(null);
    setBtError("");
    try {
      const result = await api.admin.runBacktest(id, btDate);
      setBtResult(result);
      await loadBtHistory();
    } catch (e: unknown) {
      setBtError(e instanceof Error ? e.message : "백테스트 실패");
    } finally {
      setBtRunning(false);
    }
  }

  if (loading)
    return (
      <div className="min-h-screen bg-gray-900 flex items-center justify-center text-gray-400">
        로딩 중...
      </div>
    );

  const currentRun = runs.find((r) => r.run_id === selected);
  const pending    = (stats?.total_picks ?? 0) - (stats?.total_verified ?? 0);

  return (
    <div className="min-h-screen bg-gray-900 text-white p-6">
      <div className="max-w-5xl mx-auto">

        {/* 헤더 */}
        <div className="flex items-center gap-3 mb-6">
          <Link href="/" className="text-gray-400 hover:text-white text-sm">← 대시보드</Link>
          <span className="text-gray-600">/</span>
          <h1 className="text-xl font-bold">전략 상세</h1>
        </div>

        {/* 탭 */}
        <div className="flex gap-1 mb-6 bg-gray-800 p-1 rounded-xl w-fit">
          {(["live", "backtest"] as Tab[]).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`px-5 py-2 rounded-lg text-sm font-medium transition-colors ${
                tab === t ? "bg-blue-600 text-white" : "text-gray-400 hover:text-white"
              }`}
            >
              {t === "live" ? "추천 결과" : "백테스트"}
            </button>
          ))}
        </div>

        {/* ── 추천 결과 탭 ─────────────────────────────────── */}
        {tab === "live" && (
          <>
            {stats && (
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-6">
                <StatCard label="총 실행" value={stats.total_runs} sub="회" />
                <StatCard
                  label="승률"
                  value={stats.win_rate != null ? `${(stats.win_rate * 100).toFixed(1)}%` : "-"}
                  sub={stats.total_verified > 0 ? `${stats.success_count}승 ${stats.fail_count}패` : "검증 대기 중"}
                  color={stats.win_rate != null ? (stats.win_rate >= 0.5 ? "green" : "red") : "gray"}
                />
                <StatCard
                  label="평균 수익률"
                  value={stats.avg_pnl_pct != null ? `${stats.avg_pnl_pct.toFixed(2)}%` : "-"}
                  color={stats.avg_pnl_pct != null ? (stats.avg_pnl_pct >= 0 ? "green" : "red") : "gray"}
                />
                <StatCard
                  label="기댓값"
                  value={stats.expected_value != null ? `${stats.expected_value.toFixed(2)}%` : "-"}
                  sub={`검증 ${stats.total_verified}건`}
                  color={stats.expected_value != null ? (stats.expected_value >= 0 ? "green" : "red") : "gray"}
                />
              </div>
            )}

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
              <div className="flex flex-col gap-4">
                {stats && (
                  <div className="bg-gray-800 rounded-2xl p-4">
                    <h3 className="text-sm font-medium text-gray-400 mb-3">검증 현황</h3>
                    <WinRateChart success={stats.success_count} fail={stats.fail_count} pending={pending} />
                  </div>
                )}
                <div className="bg-gray-800 rounded-2xl p-4">
                  <h3 className="text-sm font-medium text-gray-400 mb-3">실행 이력 ({runs.length}회)</h3>
                  {runs.length === 0 ? (
                    <p className="text-gray-500 text-sm">실행 이력 없음</p>
                  ) : (
                    <div className="flex flex-col gap-1">
                      {runs.map((run) => (
                        <button
                          key={run.run_id}
                          onClick={() => setSelected(run.run_id)}
                          className={`text-left px-3 py-2 rounded-lg text-sm transition-colors ${
                            selected === run.run_id ? "bg-blue-600 text-white" : "text-gray-400 hover:bg-gray-700"
                          }`}
                        >
                          <div>{run.run_date}</div>
                          <div className="text-xs opacity-70">{run.recommendations.length}개 · {run.ai_model_used ?? "-"}</div>
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              </div>

              <div className="lg:col-span-2 bg-gray-800 rounded-2xl p-6">
                {currentRun ? (
                  <>
                    <div className="flex items-center justify-between mb-4">
                      <h3 className="font-semibold">{currentRun.run_date} 추천 종목</h3>
                      <span className="text-xs text-gray-500">{currentRun.prompt_version}</span>
                    </div>
                    <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-4">
                      {(["stage1_model","stage2_model","stage3_model","stage4_model"] as const).map((key, i) => {
                        const labels = ["매크로","역사분석","산업분석","종목선정"];
                        const model = currentRun[key];
                        return (
                          <div key={key} className="bg-gray-900 rounded-lg px-3 py-2">
                            <div className="text-xs text-gray-500 mb-0.5">S{i+1} {labels[i]}</div>
                            <div className="text-xs text-gray-300 truncate" title={model ?? "-"}>
                              {model ? model.replace("gemini-","").replace("-preview","★") : <span className="text-gray-600">-</span>}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                    <RecommendationTable recommendations={currentRun.recommendations} />
                  </>
                ) : (
                  <p className="text-gray-500 text-sm">실행 이력을 선택하세요.</p>
                )}
              </div>
            </div>
          </>
        )}

        {/* ── 백테스트 탭 ──────────────────────────────────── */}
        {tab === "backtest" && (
          <div className="flex flex-col gap-6">

            {/* 실행 패널 */}
            <div className="bg-gray-800 rounded-2xl p-6">
              <h3 className="font-semibold mb-1">백테스트 실행</h3>
              <p className="text-xs text-gray-500 mb-4">
                기준 날짜 ±12일, 3일 간격 최대 9회 실행 · 10~15분 소요
              </p>
              <div className="flex items-end gap-3 flex-wrap">
                <div>
                  <label className="text-xs text-gray-400 mb-1 block">기준 날짜</label>
                  <input
                    type="date"
                    value={btDate}
                    onChange={(e) => setBtDate(e.target.value)}
                    className="bg-gray-700 rounded-lg px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-500"
                  />
                </div>
                <button
                  onClick={runBacktest}
                  disabled={btRunning}
                  className="bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg px-5 py-2 text-sm font-medium flex items-center gap-2"
                >
                  {btRunning && (
                    <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"/>
                    </svg>
                  )}
                  {btRunning ? "실행 중..." : "백테스트 실행"}
                </button>
              </div>
              {btError && <p className="text-red-400 text-sm mt-3">{btError}</p>}
              {btRunning && (
                <p className="text-yellow-400 text-xs mt-3">AI가 과거 데이터로 종목을 분석 중이에요. 완료될 때까지 기다려주세요.</p>
              )}
            </div>

            {/* 방금 실행 결과 */}
            {btResult && (
              <div className="bg-gray-800 rounded-2xl p-6">
                <div className="flex items-center justify-between mb-4">
                  <h3 className="font-semibold">실행 결과 — {btResult.base_date} 기준</h3>
                  <span className="text-xs text-gray-500">{btResult.dates_succeeded}/{btResult.dates_attempted}개 날짜 성공</span>
                </div>

                {/* 종합 요약 */}
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-5">
                  <StatCard label="총 픽" value={btResult.summary.total_picks} sub="개" />
                  <StatCard
                    label="목표 달성률"
                    value={btResult.summary.win_rate != null ? `${(btResult.summary.win_rate * 100).toFixed(1)}%` : "-"}
                    sub={`${btResult.summary.success_count}성공 ${btResult.summary.fail_count}실패`}
                    color={btResult.summary.win_rate != null ? (btResult.summary.win_rate >= 0.3 ? "green" : "red") : "gray"}
                  />
                  <StatCard
                    label="평균 수익률"
                    value={btResult.summary.avg_pnl != null ? `${btResult.summary.avg_pnl.toFixed(2)}%` : "-"}
                    color={btResult.summary.avg_pnl != null ? (btResult.summary.avg_pnl >= 0 ? "green" : "red") : "gray"}
                  />
                  <StatCard label="스킵 날짜" value={btResult.skipped.length} sub="개" color="gray" />
                </div>

                {/* 날짜별 결과 */}
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-gray-400 border-b border-gray-700">
                        <th className="pb-2 text-left">날짜</th>
                        <th className="pb-2 text-right">픽 수</th>
                        <th className="pb-2 text-right">달성률</th>
                        <th className="pb-2 text-right">평균 수익</th>
                        <th className="pb-2 text-left pl-4">픽 종목</th>
                      </tr>
                    </thead>
                    <tbody>
                      {btResult.results.map((r) => (
                        <tr key={r.date} className="border-b border-gray-800">
                          <td className="py-2 text-gray-300">{r.date}</td>
                          <td className="py-2 text-right">{r.picks.length}</td>
                          <td className={`py-2 text-right font-medium ${
                            r.win_rate == null ? "text-gray-500" :
                            r.win_rate >= 0.4 ? "text-green-400" : "text-red-400"
                          }`}>
                            {r.win_rate != null ? `${(r.win_rate * 100).toFixed(0)}%` : r.error ? "오류" : "-"}
                          </td>
                          <td className={`py-2 text-right ${
                            r.avg_pnl == null ? "text-gray-500" :
                            r.avg_pnl >= 0 ? "text-green-400" : "text-red-400"
                          }`}>
                            {r.avg_pnl != null ? `${r.avg_pnl >= 0 ? "+" : ""}${r.avg_pnl.toFixed(2)}%` : "-"}
                          </td>
                          <td className="py-2 pl-4 text-gray-400 text-xs">
                            {r.picks.map((p) => (
                              <span
                                key={p.stock_code}
                                className={`inline-block mr-1 px-1.5 py-0.5 rounded text-xs ${
                                  p.result === "SUCCESS" ? "bg-green-900/60 text-green-300" : "bg-gray-700 text-gray-400"
                                }`}
                                title={`${p.pnl_pct != null ? (p.pnl_pct >= 0 ? "+" : "") + p.pnl_pct.toFixed(1) + "%" : ""}`}
                              >
                                {p.stock_name}
                              </span>
                            ))}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                {btResult.skipped.length > 0 && (
                  <div className="mt-3 text-xs text-gray-600">
                    스킵: {btResult.skipped.join(" · ")}
                  </div>
                )}
              </div>
            )}

            {/* 과거 백테스트 이력 */}
            <div className="bg-gray-800 rounded-2xl p-6">
              <h3 className="font-semibold mb-4">백테스트 이력</h3>
              {btHistoryLoading ? (
                <p className="text-gray-500 text-sm">로딩 중...</p>
              ) : btHistory.length === 0 ? (
                <p className="text-gray-500 text-sm">백테스트 이력이 없습니다.</p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-gray-400 border-b border-gray-700">
                        <th className="pb-2 text-left">날짜</th>
                        <th className="pb-2 text-right">픽</th>
                        <th className="pb-2 text-right">검증</th>
                        <th className="pb-2 text-right">성공</th>
                        <th className="pb-2 text-right">달성률</th>
                      </tr>
                    </thead>
                    <tbody>
                      {btHistory.map((r) => (
                        <tr key={r.run_id} className="border-b border-gray-800 text-gray-300">
                          <td className="py-2">{r.run_date}</td>
                          <td className="py-2 text-right">{r.picks}</td>
                          <td className="py-2 text-right">{r.verified}</td>
                          <td className="py-2 text-right text-green-400">{r.success}</td>
                          <td className={`py-2 text-right font-medium ${
                            r.verified === 0 ? "text-gray-500" :
                            r.success / r.verified >= 0.4 ? "text-green-400" : "text-red-400"
                          }`}>
                            {r.verified > 0 ? `${(r.success / r.verified * 100).toFixed(0)}%` : "-"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

          </div>
        )}
      </div>
    </div>
  );
}

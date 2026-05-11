"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import {
  api, RecommendationRun, StrategyStats, BacktestResult, BacktestRunSummary, BacktestOverallSummary,
  Subscription, BrokerAccount, getToken,
} from "@/lib/api";
import {
  ComposedChart, Line, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ReferenceLine, ResponsiveContainer,
} from "recharts";
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

  // ── 구독 상태 ───────────────────────────────────────────
  const [subscription, setSubscription]   = useState<Subscription | null>(null);
  const [accounts, setAccounts]           = useState<BrokerAccount[]>([]);
  const [showSubForm, setShowSubForm]     = useState(false);
  const [subAccountId, setSubAccountId]  = useState("");
  const [subAmount, setSubAmount]         = useState("100000");
  const [subAutoTrade, setSubAutoTrade]   = useState(false);
  const [subLoading, setSubLoading]       = useState(false);
  const [subError, setSubError]           = useState("");
  const [editingAmount, setEditingAmount] = useState(false);
  const [editAmount, setEditAmount]       = useState("");

  // ── 백테스트 탭 상태 ────────────────────────────────────
  const [btDate, setBtDate]           = useState("");
  const [btRunning, setBtRunning]     = useState(false);
  const [btResult, setBtResult]       = useState<BacktestResult | null>(null);
  const [btHistory, setBtHistory]     = useState<BacktestRunSummary[]>([]);
  const [btSummary, setBtSummary]     = useState<BacktestOverallSummary | null>(null);
  const [btError, setBtError]         = useState("");
  const [btHistoryLoading, setBtHistoryLoading] = useState(false);

  useEffect(() => {
    if (!getToken()) { router.push("/login"); return; }
    loadLive();
    loadSubscription();
  }, [id]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (tab === "backtest") loadBtHistory();
  }, [tab]); // eslint-disable-line react-hooks/exhaustive-deps

  async function loadSubscription() {
    try {
      const [me, subs] = await Promise.all([api.auth.me(), api.strategies.mySubscriptions()]);
      const mine = subs.find((s) => s.strategy_id === id) ?? null;
      setSubscription(mine);
      const accs = await api.users.listBrokerAccounts(me.user_id);
      setAccounts(accs.filter((a) => a.is_active));
      if (accs.length > 0 && !subAccountId) setSubAccountId(accs[0].account_id);
    } catch { /* 조용히 */ }
  }

  async function handleSubscribe() {
    if (!subAccountId) { setSubError("계좌를 선택하세요"); return; }
    const amount = parseInt(subAmount.replace(/,/g, ""), 10);
    if (!amount || amount < 10000) { setSubError("최소 10,000원 이상 입력하세요"); return; }
    setSubLoading(true); setSubError("");
    try {
      const sub = await api.strategies.subscribe({
        strategy_id: id, account_id: subAccountId,
        invest_amount_per_pick: amount, is_auto_trade: subAutoTrade,
      });
      setSubscription(sub);
      setShowSubForm(false);
    } catch (e: unknown) {
      setSubError(e instanceof Error ? e.message : "구독 실패");
    } finally { setSubLoading(false); }
  }

  async function handleUnsubscribe() {
    if (!subscription) return;
    if (!confirm("구독을 해지하시겠습니까?\n기존 보유 포지션은 원래 전략 조건대로 계속 관리됩니다.")) return;
    try {
      await api.strategies.unsubscribe(subscription.id);
      setSubscription(null);
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "해지 실패");
    }
  }

  async function handleToggleAutoTrade() {
    if (!subscription) return;
    try {
      const updated = await api.strategies.toggleAutoTrade(subscription.id);
      setSubscription(updated);
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "변경 실패");
    }
  }

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
      const [histData, summaryData] = await Promise.all([
        api.admin.getBacktestResults(id),
        api.admin.getBacktestSummary(id).catch(() => null),
      ]);
      setBtHistory(histData);
      setBtSummary(summaryData);
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

        {/* 구독 카드 */}
        <div className="bg-gray-800 rounded-2xl p-4 mb-6">
          {subscription ? (
            <div className="flex flex-wrap items-center gap-4">
              <span className="text-xs text-green-400 font-semibold bg-green-900/40 px-2 py-1 rounded-full">구독중</span>
              {editingAmount ? (
                <div className="flex items-center gap-2">
                  <input
                    type="number"
                    value={editAmount}
                    onChange={(e) => setEditAmount(e.target.value)}
                    min={10000}
                    step={10000}
                    className="bg-gray-700 rounded-lg px-2 py-1 text-sm w-32 outline-none focus:ring-2 focus:ring-blue-500"
                    autoFocus
                  />
                  <span className="text-sm text-gray-400">원</span>
                  <button
                    onClick={async () => {
                      const amount = parseInt(editAmount.replace(/,/g, ""), 10);
                      if (!amount || amount < 10000) return;
                      const updated = await api.strategies.updateSubscription(subscription.id, { invest_amount_per_pick: amount });
                      setSubscription(updated);
                      setEditingAmount(false);
                    }}
                    className="text-xs bg-blue-600 hover:bg-blue-700 text-white px-2 py-1 rounded"
                  >확인</button>
                  <button onClick={() => setEditingAmount(false)} className="text-xs text-gray-400 hover:text-white">취소</button>
                </div>
              ) : (
                <button
                  onClick={() => { setEditAmount(String(Number(subscription.invest_amount_per_pick))); setEditingAmount(true); }}
                  className="text-sm text-gray-300 hover:text-white group"
                >
                  종목당 <span className="text-white font-medium">{Number(subscription.invest_amount_per_pick).toLocaleString()}원</span>
                  <span className="text-gray-500 text-xs ml-1 group-hover:text-gray-300">✎</span>
                </button>
              )}
              <div className="flex items-center gap-2">
                <span className="text-sm text-gray-400">자동매매</span>
                <button
                  onClick={handleToggleAutoTrade}
                  className={`relative w-11 h-6 rounded-full transition-colors ${subscription.is_auto_trade ? "bg-blue-600" : "bg-gray-600"}`}
                >
                  <span className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full shadow transition-transform ${subscription.is_auto_trade ? "translate-x-5" : "translate-x-0"}`} />
                </button>
                <span className={`text-xs font-medium ${subscription.is_auto_trade ? "text-blue-400" : "text-gray-500"}`}>
                  {subscription.is_auto_trade ? "ON" : "OFF"}
                </span>
              </div>
              <button
                onClick={handleUnsubscribe}
                className="ml-auto text-xs text-red-400 hover:text-red-300 border border-red-800 hover:border-red-600 px-3 py-1.5 rounded-lg transition-colors"
              >
                구독 해지
              </button>
            </div>
          ) : showSubForm ? (
            <div className="flex flex-col gap-3">
              <p className="text-sm font-medium text-gray-300">전략 구독</p>
              <div className="flex flex-wrap gap-3 items-end">
                <div>
                  <label className="text-xs text-gray-400 mb-1 block">계좌</label>
                  <select
                    value={subAccountId}
                    onChange={(e) => setSubAccountId(e.target.value)}
                    className="bg-gray-700 rounded-lg px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-500 min-w-[180px]"
                  >
                    {accounts.length === 0 && <option value="">계좌 없음</option>}
                    {accounts.map((a) => (
                      <option key={a.account_id} value={a.account_id}>
                        {a.broker} {a.account_no} ({a.account_type})
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="text-xs text-gray-400 mb-1 block">종목당 투자금 (원)</label>
                  <input
                    type="number"
                    value={subAmount}
                    onChange={(e) => setSubAmount(e.target.value)}
                    min={10000}
                    step={10000}
                    className="bg-gray-700 rounded-lg px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-500 w-36"
                  />
                </div>
                <div className="flex items-center gap-2 pb-1.5">
                  <span className="text-sm text-gray-400">자동매매</span>
                  <button
                    onClick={() => setSubAutoTrade(!subAutoTrade)}
                    className={`relative w-11 h-6 rounded-full transition-colors ${subAutoTrade ? "bg-blue-600" : "bg-gray-600"}`}
                  >
                    <span className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full shadow transition-transform ${subAutoTrade ? "translate-x-5" : "translate-x-0"}`} />
                  </button>
                </div>
                <button
                  onClick={handleSubscribe}
                  disabled={subLoading || accounts.length === 0}
                  className="bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg px-4 py-2 text-sm font-medium"
                >
                  {subLoading ? "처리 중..." : "구독 확인"}
                </button>
                <button
                  onClick={() => { setShowSubForm(false); setSubError(""); }}
                  className="text-gray-400 hover:text-white text-sm px-3 py-2"
                >
                  취소
                </button>
              </div>
              {subError && <p className="text-red-400 text-xs">{subError}</p>}
            </div>
          ) : (
            <div className="flex items-center justify-between">
              <p className="text-sm text-gray-400">이 전략을 구독하면 추천 알림과 자동매매를 사용할 수 있습니다.</p>
              <button
                onClick={() => setShowSubForm(true)}
                className="bg-blue-600 hover:bg-blue-700 text-white rounded-lg px-4 py-2 text-sm font-medium whitespace-nowrap ml-4"
              >
                구독하기
              </button>
            </div>
          )}
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
            {stats && (() => {
              const aiVsRandom = stats.avg_pnl_pct != null && stats.random_avg_pnl != null
                ? stats.avg_pnl_pct - stats.random_avg_pnl : null;
              return (
              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 mb-6">
                <StatCard label="총 실행" value={stats.total_runs} sub="회" />
                <StatCard
                  label="승률"
                  value={stats.win_rate != null ? `${(stats.win_rate * 100).toFixed(1)}%` : "-"}
                  sub={stats.total_verified > 0 ? `${stats.success_count}승 ${stats.fail_count}패` : "검증 대기 중"}
                  color={stats.win_rate != null ? (stats.win_rate >= 0.5 ? "green" : "red") : "gray"}
                />
                <StatCard
                  label="AI 평균수익"
                  value={stats.avg_pnl_pct != null ? `${stats.avg_pnl_pct >= 0 ? "+" : ""}${stats.avg_pnl_pct.toFixed(2)}%` : "-"}
                  color={stats.avg_pnl_pct != null ? (stats.avg_pnl_pct >= 0 ? "green" : "red") : "gray"}
                />
                <StatCard
                  label="랜덤 평균수익"
                  value={stats.random_avg_pnl != null ? `${stats.random_avg_pnl >= 0 ? "+" : ""}${stats.random_avg_pnl.toFixed(2)}%` : "-"}
                  sub="시장 효과"
                  color={stats.random_avg_pnl != null ? (stats.random_avg_pnl >= 0 ? "green" : "red") : "gray"}
                />
                <StatCard
                  label="AI 우위"
                  value={aiVsRandom != null ? `${aiVsRandom >= 0 ? "+" : ""}${aiVsRandom.toFixed(2)}%p` : "-"}
                  sub="AI - 랜덤"
                  color={aiVsRandom != null ? (aiVsRandom >= 0 ? "green" : "red") : "gray"}
                />
                <StatCard
                  label="성공/실패 평균"
                  value={stats.success_avg_pnl != null ? `${stats.success_avg_pnl >= 0 ? "+" : ""}${stats.success_avg_pnl.toFixed(1)}%` : "-"}
                  sub={stats.fail_avg_pnl != null ? `실패 ${stats.fail_avg_pnl >= 0 ? "+" : ""}${stats.fail_avg_pnl.toFixed(1)}%` : "실패 데이터 없음"}
                  color={stats.success_avg_pnl != null ? "green" : "gray"}
                />
              </div>
              );
            })()}

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
        {tab === "backtest" && (() => {
          const s = btSummary;
          const fmt = (v: number | null | undefined, digits = 2) =>
            v != null ? `${v >= 0 ? "+" : ""}${v.toFixed(digits)}%` : "-";

          return (
          <div className="flex flex-col gap-6">

            {/* 백테스트 종합 요약 */}
            {s && s.total_runs > 0 && (
              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
                <StatCard label="총 실행" value={s.total_runs} sub={`${s.total_picks}픽`} />
                <StatCard
                  label="목표 달성률"
                  value={s.win_rate != null ? `${(s.win_rate * 100).toFixed(1)}%` : "-"}
                  sub={s.win_rate != null ? `${Math.round(s.win_rate * s.total_picks)}성공` : ""}
                  color={s.win_rate != null ? (s.win_rate >= 0.4 ? "green" : "red") : "gray"}
                />
                <StatCard
                  label="AI 평균수익"
                  value={fmt(s.ai_avg_pnl)}
                  color={s.ai_avg_pnl != null ? (s.ai_avg_pnl >= 0 ? "green" : "red") : "gray"}
                />
                <StatCard
                  label="랜덤 평균수익"
                  value={fmt(s.rand_avg_pnl)}
                  sub="시장 효과"
                  color={s.rand_avg_pnl != null ? (s.rand_avg_pnl >= 0 ? "green" : "red") : "gray"}
                />
                <StatCard
                  label="AI 우위"
                  value={s.advantage != null ? `${s.advantage >= 0 ? "+" : ""}${s.advantage.toFixed(2)}%p` : "-"}
                  sub="AI - 랜덤"
                  color={s.advantage != null ? (s.advantage >= 0 ? "green" : "red") : "gray"}
                />
                <StatCard
                  label="성공/실패 평균"
                  value={fmt(s.ai_success_avg, 1)}
                  sub={s.ai_fail_avg != null ? `실패 ${fmt(s.ai_fail_avg, 1)}` : "실패 데이터 없음"}
                  color={s.ai_success_avg != null ? "green" : "gray"}
                />
              </div>
            )}

            {/* 월별 AI vs 랜덤 차트 */}
            {s && s.monthly.length > 0 && (
              <div className="bg-gray-800 rounded-2xl p-6">
                <h3 className="font-semibold mb-1">월별 성과 추이</h3>
                <p className="text-xs text-gray-500 mb-4">AI 평균수익 vs 랜덤 대조군 (막대: 월별 AI 우위)</p>
                <ResponsiveContainer width="100%" height={260}>
                  <ComposedChart data={s.monthly} margin={{ top: 5, right: 20, left: 0, bottom: 5 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
                    <XAxis dataKey="month" tick={{ fill: "#9ca3af", fontSize: 12 }} />
                    <YAxis
                      yAxisId="pnl"
                      tickFormatter={(v) => `${v}%`}
                      tick={{ fill: "#9ca3af", fontSize: 11 }}
                      width={48}
                    />
                    <YAxis
                      yAxisId="wr"
                      orientation="right"
                      tickFormatter={(v) => `${(v * 100).toFixed(0)}%`}
                      tick={{ fill: "#6b7280", fontSize: 11 }}
                      width={40}
                      domain={[0, 1]}
                    />
                    <Tooltip
                      contentStyle={{ background: "#1f2937", border: "none", borderRadius: 8 }}
                      labelStyle={{ color: "#e5e7eb" }}
                      formatter={(value, name) => {
                        const v = Number(value);
                        if (name === "승률") return [`${(v * 100).toFixed(1)}%`, name];
                        return [`${v >= 0 ? "+" : ""}${v.toFixed(2)}%`, String(name)];
                      }}
                    />
                    <Legend wrapperStyle={{ fontSize: 12, color: "#9ca3af" }} />
                    <ReferenceLine yAxisId="pnl" y={0} stroke="#4b5563" strokeDasharray="4 2" />
                    <Bar yAxisId="pnl" dataKey="advantage" name="AI 우위" fill="#3b82f6" opacity={0.35} radius={[3, 3, 0, 0]} />
                    <Line yAxisId="pnl" type="monotone" dataKey="ai_avg_pnl" name="AI 수익" stroke="#60a5fa" strokeWidth={2} dot={{ r: 4 }} />
                    <Line yAxisId="pnl" type="monotone" dataKey="rand_avg_pnl" name="랜덤 수익" stroke="#6b7280" strokeWidth={2} strokeDasharray="5 3" dot={{ r: 4 }} />
                    <Line yAxisId="wr" type="monotone" dataKey="win_rate" name="승률" stroke="#34d399" strokeWidth={1.5} dot={false} />
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
            )}

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
                        <th className="pb-2 text-right">달성률</th>
                        <th className="pb-2 text-right">AI 수익</th>
                        <th className="pb-2 text-right">랜덤 수익</th>
                        <th className="pb-2 text-right">우위</th>
                        <th className="pb-2 text-left pl-4">픽 종목</th>
                      </tr>
                    </thead>
                    <tbody>
                      {btResult.results.map((r) => {
                        const diff = r.avg_pnl != null && r.random_avg_pnl != null ? r.avg_pnl - r.random_avg_pnl : null;
                        return (
                        <tr key={r.date} className="border-b border-gray-800">
                          <td className="py-2 text-gray-300">{r.date}</td>
                          <td className={`py-2 text-right font-medium ${
                            r.win_rate == null ? "text-gray-500" :
                            r.win_rate >= 0.4 ? "text-green-400" : "text-red-400"
                          }`}>
                            {r.win_rate != null ? `${(r.win_rate * 100).toFixed(0)}%` : r.error ? "오류" : "-"}
                          </td>
                          <td className={`py-2 text-right ${r.avg_pnl == null ? "text-gray-500" : r.avg_pnl >= 0 ? "text-green-400" : "text-red-400"}`}>
                            {r.avg_pnl != null ? `${r.avg_pnl >= 0 ? "+" : ""}${r.avg_pnl.toFixed(2)}%` : "-"}
                          </td>
                          <td className={`py-2 text-right ${r.random_avg_pnl == null ? "text-gray-500" : r.random_avg_pnl >= 0 ? "text-green-400" : "text-red-400"}`}>
                            {r.random_avg_pnl != null ? `${r.random_avg_pnl >= 0 ? "+" : ""}${r.random_avg_pnl.toFixed(2)}%` : "-"}
                          </td>
                          <td className={`py-2 text-right font-medium ${diff == null ? "text-gray-500" : diff >= 0 ? "text-blue-400" : "text-orange-400"}`}>
                            {diff != null ? `${diff >= 0 ? "+" : ""}${diff.toFixed(2)}%p` : "-"}
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
                        );
                      })}
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
                        <th className="pb-2 text-right">달성률</th>
                        <th className="pb-2 text-right">AI 수익</th>
                        <th className="pb-2 text-right">랜덤 수익</th>
                        <th className="pb-2 text-right">우위</th>
                      </tr>
                    </thead>
                    <tbody>
                      {btHistory.map((r) => {
                        const d = r.avg_pnl != null && r.random_avg_pnl != null ? r.avg_pnl - r.random_avg_pnl : null;
                        return (
                        <tr key={r.run_id} className="border-b border-gray-800 text-gray-300">
                          <td className="py-2">{r.run_date}</td>
                          <td className={`py-2 text-right font-medium ${r.verified === 0 ? "text-gray-500" : r.success/r.verified >= 0.4 ? "text-green-400" : "text-red-400"}`}>
                            {r.verified > 0 ? `${(r.success/r.verified*100).toFixed(0)}%` : "-"}
                          </td>
                          <td className={`py-2 text-right ${r.avg_pnl == null ? "text-gray-500" : r.avg_pnl >= 0 ? "text-green-400" : "text-red-400"}`}>
                            {r.avg_pnl != null ? `${r.avg_pnl >= 0 ? "+" : ""}${r.avg_pnl.toFixed(2)}%` : "-"}
                          </td>
                          <td className={`py-2 text-right ${r.random_avg_pnl == null ? "text-gray-500" : r.random_avg_pnl >= 0 ? "text-green-400" : "text-red-400"}`}>
                            {r.random_avg_pnl != null ? `${r.random_avg_pnl >= 0 ? "+" : ""}${r.random_avg_pnl.toFixed(2)}%` : "-"}
                          </td>
                          <td className={`py-2 text-right font-medium ${d == null ? "text-gray-500" : d >= 0 ? "text-blue-400" : "text-orange-400"}`}>
                            {d != null ? `${d >= 0 ? "+" : ""}${d.toFixed(2)}%p` : "-"}
                          </td>
                        </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

          </div>
          );
        })()}
      </div>
    </div>
  );
}

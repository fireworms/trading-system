"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { api, Strategy, StrategyStats, Subscription, User, CandidateFilter, CandidateMarket, getToken } from "@/lib/api";
import StatCard from "@/components/StatCard";

interface StrategyWithStats {
  strategy: Strategy;
  stats: StrategyStats | null;
}

const FILTER_LABELS: Record<CandidateFilter, string> = {
  volume:   "거래량 위주",
  largecap: "대형주 위주",
  mixed:    "혼합",
};
const FILTER_COLORS: Record<CandidateFilter, string> = {
  volume:   "bg-orange-900/60 text-orange-300",
  largecap: "bg-blue-900/60 text-blue-300",
  mixed:    "bg-gray-700 text-gray-300",
};
const MARKET_LABELS: Record<CandidateMarket, string> = {
  KOSPI:  "KOSPI",
  KOSDAQ: "KOSDAQ",
  NAS:    "NASDAQ",
  ALL:    "전 시장",
};
const MARKET_COLORS: Record<CandidateMarket, string> = {
  KOSPI:  "bg-indigo-900/60 text-indigo-300",
  KOSDAQ: "bg-green-900/60 text-green-300",
  NAS:    "bg-purple-900/60 text-purple-300",
  ALL:    "bg-gray-700 text-gray-300",
};

const DEFAULT_FORM = {
  name: "", description: "",
  hold_days: 10, target_pct: "15", stop_loss_pct: "7",
  min_probability: "60", pick_count: 5, run_interval_days: 3,
  candidate_filter: "mixed" as CandidateFilter,
  candidate_market: "ALL" as CandidateMarket,
};

export default function DashboardPage() {
  const router = useRouter();
  const [me, setMe]             = useState<User | null>(null);
  const [items, setItems]       = useState<StrategyWithStats[]>([]);
  const [subMap, setSubMap]     = useState<Map<string, Subscription>>(new Map());
  const [scheduler, setScheduler] = useState<{ running: boolean; jobs: { id: string; next_run: string | null }[] } | null>(null);
  const [loading, setLoading]   = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm]         = useState(DEFAULT_FORM);
  const [creating, setCreating] = useState(false);
  const [msg, setMsg]           = useState("");

  useEffect(() => {
    if (!getToken()) { router.push("/login"); return; }
    load();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function load() {
    try {
      const user = await api.auth.me();
      setMe(user);
      const isAdmin = user.role === "ADMIN" || user.role === "SUPER_ADMIN";
      const [strategies, sched, subs] = await Promise.all([
        api.strategies.list(),
        isAdmin ? api.admin.schedulerStatus().catch(() => null) : Promise.resolve(null),
        api.strategies.mySubscriptions().catch(() => [] as Subscription[]),
      ]);
      setSubMap(new Map(subs.map((s) => [s.strategy_id, s])));
      const withStats = await Promise.all(
        strategies.map(async (s) => ({
          strategy: s,
          stats: await api.recommendations.stats(s.strategy_id).catch(() => null),
        }))
      );
      setItems(withStats);
      setScheduler(sched);
    } catch {
      router.push("/login");
    } finally {
      setLoading(false);
    }
  }

  async function createStrategy() {
    if (!form.name.trim()) { setMsg("전략명을 입력하세요"); return; }
    setCreating(true); setMsg("");
    try {
      await api.strategies.create({
        name:              form.name.trim(),
        description:       form.description.trim() || null,
        hold_days:         Number(form.hold_days),
        target_pct:        form.target_pct,
        stop_loss_pct:     form.stop_loss_pct,
        min_probability:   form.min_probability,
        pick_count:        Number(form.pick_count),
        run_interval_days: Number(form.run_interval_days),
        candidate_filter:  form.candidate_filter,
        candidate_market:  form.candidate_market,
      });
      setForm(DEFAULT_FORM);
      setShowCreate(false);
      await load();
    } catch (e: unknown) {
      setMsg(e instanceof Error ? e.message : "생성 실패");
    } finally {
      setCreating(false);
    }
  }

  if (loading)
    return (
      <div className="min-h-screen bg-gray-900 flex items-center justify-center text-gray-400">
        로딩 중...
      </div>
    );

  const nextStrategyRun = scheduler?.jobs.find((j) => j.id === "run_strategies")?.next_run;
  const isAdmin = me?.role === "ADMIN" || me?.role === "SUPER_ADMIN";

  return (
    <div className="min-h-screen bg-gray-900 text-white p-6">
      <div className="max-w-5xl mx-auto">

        {/* 헤더 */}
        <div className="flex items-center justify-between mb-8">
          <h1 className="text-2xl font-bold">Trading Dashboard</h1>
          <div className="flex items-center gap-3 text-sm">
            <span className={`w-2 h-2 rounded-full ${scheduler?.running ? "bg-green-400" : "bg-red-400"}`} />
            <span className="text-gray-400">
              스케줄러 {scheduler?.running ? "실행 중" : "정지"}
              {nextStrategyRun &&
                ` · 다음 실행: ${new Date(nextStrategyRun).toLocaleString("ko-KR")}`}
            </span>
            {isAdmin && (
              <button
                onClick={() => { setShowCreate((v) => !v); setMsg(""); }}
                className="bg-blue-600 hover:bg-blue-700 text-white rounded-lg px-3 py-1.5 text-xs font-medium"
              >
                {showCreate ? "닫기" : "+ 전략 만들기"}
              </button>
            )}
          </div>
        </div>

        {/* 전략 생성 폼 */}
        {showCreate && (
          <div className="bg-gray-800 rounded-2xl p-6 mb-6">
            <h2 className="font-semibold text-gray-200 mb-4">새 전략</h2>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div className="sm:col-span-2">
                <label className="text-xs text-gray-400 mb-1 block">전략명 *</label>
                <input value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                  className="w-full bg-gray-700 rounded-lg px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-500"
                  placeholder="단기 거래량 전략" />
              </div>
              <div className="sm:col-span-2">
                <label className="text-xs text-gray-400 mb-1 block">설명</label>
                <input value={form.description} onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
                  className="w-full bg-gray-700 rounded-lg px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-500"
                  placeholder="선택 사항" />
              </div>

              {/* 후보 풀 설정 */}
              <div>
                <label className="text-xs text-gray-400 mb-1 block">후보 필터</label>
                <select value={form.candidate_filter}
                  onChange={(e) => setForm((f) => ({ ...f, candidate_filter: e.target.value as CandidateFilter }))}
                  className="w-full bg-gray-700 rounded-lg px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-500">
                  <option value="volume">거래량 위주 — 단기 고수익 가능성</option>
                  <option value="largecap">대형주 위주 — 안정적 유동성</option>
                  <option value="mixed">혼합 — 거래량 + 대형주 + 균등 샘플</option>
                </select>
              </div>
              <div>
                <label className="text-xs text-gray-400 mb-1 block">후보 시장</label>
                <select value={form.candidate_market}
                  onChange={(e) => setForm((f) => ({ ...f, candidate_market: e.target.value as CandidateMarket }))}
                  className="w-full bg-gray-700 rounded-lg px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-500">
                  <option value="ALL">전 시장 (KOSPI + KOSDAQ + NASDAQ)</option>
                  <option value="KOSPI">KOSPI만</option>
                  <option value="KOSDAQ">KOSDAQ만</option>
                  <option value="NAS">NASDAQ만</option>
                </select>
              </div>

              {/* 수치 파라미터 */}
              {([
                ["보유기간 (일)", "hold_days", 1, 365],
                ["목표수익률 (%)", "target_pct", 0.1, 100],
                ["손절라인 (%)", "stop_loss_pct", 0.1, 50],
                ["최소확률 (%)", "min_probability", 0, 100],
                ["픽 종목 수", "pick_count", 1, 20],
                ["실행 주기 (일)", "run_interval_days", 1, 30],
              ] as [string, keyof typeof form, number, number][]).map(([label, key, min, max]) => (
                <div key={key}>
                  <label className="text-xs text-gray-400 mb-1 block">{label}</label>
                  <input type="number" min={min} max={max}
                    value={form[key] as string | number}
                    onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))}
                    className="w-full bg-gray-700 rounded-lg px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-500" />
                </div>
              ))}
            </div>

            {msg && <p className="text-sm text-red-400 mt-3">{msg}</p>}
            <button onClick={createStrategy} disabled={creating}
              className="mt-4 bg-blue-600 hover:bg-blue-700 text-white rounded-lg px-5 py-2 text-sm font-medium disabled:opacity-50">
              {creating ? "생성 중..." : "전략 생성"}
            </button>
          </div>
        )}

        {/* 전략 카드 목록 */}
        {items.length === 0 ? (
          <p className="text-gray-500">등록된 전략이 없습니다. {isAdmin && "위에서 전략을 만들어보세요."}</p>
        ) : (
          <div className="flex flex-col gap-6">
            {items.map(({ strategy, stats }) => {
              const sub = subMap.get(strategy.strategy_id);
              return (
              <div key={strategy.strategy_id} className="bg-gray-800 rounded-2xl p-6">
                <div className="flex items-start justify-between mb-3">
                  <div>
                    <div className="flex items-center flex-wrap gap-2">
                      <h2 className="text-lg font-semibold">{strategy.name}</h2>
                      <span className={`text-xs px-2 py-0.5 rounded-full ${strategy.is_active ? "bg-green-900 text-green-400" : "bg-gray-700 text-gray-400"}`}>
                        {strategy.is_active ? "활성" : "비활성"}
                      </span>
                      {/* 구독 배지 */}
                      {sub && (
                        <span className="text-xs px-2 py-0.5 rounded-full bg-yellow-900/60 text-yellow-300 flex items-center gap-1">
                          ★ 구독 중
                          <span className={`ml-1 ${sub.is_auto_trade ? "text-green-400" : "text-gray-400"}`}>
                            · 자동{sub.is_auto_trade ? "ON" : "OFF"}
                          </span>
                          <span className="text-gray-400 ml-1">
                            · {Number(sub.invest_amount_per_pick).toLocaleString()}원
                          </span>
                        </span>
                      )}
                      {/* 후보 필터 배지 */}
                      <span className={`text-xs px-2 py-0.5 rounded-full ${FILTER_COLORS[strategy.candidate_filter] ?? FILTER_COLORS.mixed}`}>
                        {FILTER_LABELS[strategy.candidate_filter] ?? strategy.candidate_filter}
                      </span>
                      <span className={`text-xs px-2 py-0.5 rounded-full ${MARKET_COLORS[strategy.candidate_market] ?? MARKET_COLORS.ALL}`}>
                        {MARKET_LABELS[strategy.candidate_market] ?? strategy.candidate_market}
                      </span>
                    </div>
                    <p className="text-sm text-gray-400 mt-1">{strategy.description ?? ""}</p>
                  </div>
                  <Link
                    href={`/strategies/${strategy.strategy_id}`}
                    className="text-sm text-blue-400 hover:text-blue-300 whitespace-nowrap ml-4"
                  >
                    추천 결과 보기 →
                  </Link>
                </div>

                {/* 파라미터 */}
                <div className="flex flex-wrap gap-3 text-xs text-gray-500 mb-4">
                  <span>보유 {strategy.hold_days}일</span>
                  <span>목표 +{strategy.target_pct}%</span>
                  <span>손절 -{strategy.stop_loss_pct}%</span>
                  <span>최소확률 {strategy.min_probability}%</span>
                  <span>픽 {strategy.pick_count}개</span>
                  <span>주기 {strategy.run_interval_days}일</span>
                </div>

                {/* 성과 통계 */}
                {stats ? (() => {
                  const aiVsRandom = stats.avg_pnl_pct != null && stats.random_avg_pnl != null
                    ? stats.avg_pnl_pct - stats.random_avg_pnl : null;
                  return (
                  <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
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
                })() : (
                  <p className="text-sm text-gray-500">통계 없음</p>
                )}
              </div>
              );
            })}
          </div>
        )}

        {/* 스케줄러 잡 목록 */}
        {scheduler?.running && (
          <div className="mt-8 bg-gray-800 rounded-2xl p-6">
            <h3 className="font-semibold mb-3 text-gray-300">스케줄러 잡</h3>
            <div className="flex flex-col gap-2">
              {scheduler.jobs.map((job) => (
                <div key={job.id} className="flex justify-between text-sm">
                  <span className="text-gray-400 font-mono">{job.id}</span>
                  <span className="text-gray-300">
                    {job.next_run ? new Date(job.next_run).toLocaleString("ko-KR") : "-"}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

      </div>
    </div>
  );
}

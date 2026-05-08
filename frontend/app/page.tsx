"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { api, Strategy, StrategyStats, getToken } from "@/lib/api";
import StatCard from "@/components/StatCard";

interface StrategyWithStats {
  strategy: Strategy;
  stats: StrategyStats | null;
}

export default function DashboardPage() {
  const router = useRouter();
  const [items, setItems]       = useState<StrategyWithStats[]>([]);
  const [scheduler, setScheduler] = useState<{ running: boolean; jobs: { id: string; next_run: string | null }[] } | null>(null);
  const [loading, setLoading]   = useState(true);

  useEffect(() => {
    if (!getToken()) { router.push("/login"); return; }
    load();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function load() {
    try {
      const [strategies, sched] = await Promise.all([
        api.strategies.list(),
        api.admin.schedulerStatus(),
      ]);
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

  if (loading)
    return (
      <div className="min-h-screen bg-gray-900 flex items-center justify-center text-gray-400">
        로딩 중...
      </div>
    );

  const nextStrategyRun = scheduler?.jobs.find((j) => j.id === "run_strategies")?.next_run;

  return (
    <div className="min-h-screen bg-gray-900 text-white p-6">
      <div className="max-w-5xl mx-auto">

        {/* 헤더 */}
        <div className="flex items-center justify-between mb-8">
          <h1 className="text-2xl font-bold">Trading Dashboard</h1>
          <div className="flex items-center gap-2 text-sm">
            <span className={`w-2 h-2 rounded-full ${scheduler?.running ? "bg-green-400" : "bg-red-400"}`} />
            <span className="text-gray-400">
              스케줄러 {scheduler?.running ? "실행 중" : "정지"}
              {nextStrategyRun &&
                ` · 다음 전략 실행: ${new Date(nextStrategyRun).toLocaleString("ko-KR")}`}
            </span>
          </div>
        </div>

        {/* 전략 카드 */}
        {items.length === 0 ? (
          <p className="text-gray-500">등록된 전략이 없습니다.</p>
        ) : (
          <div className="flex flex-col gap-6">
            {items.map(({ strategy, stats }) => (
              <div key={strategy.strategy_id} className="bg-gray-800 rounded-2xl p-6">
                <div className="flex items-start justify-between mb-4">
                  <div>
                    <div className="flex items-center gap-2">
                      <h2 className="text-lg font-semibold">{strategy.name}</h2>
                      <span className={`text-xs px-2 py-0.5 rounded-full ${strategy.is_active ? "bg-green-900 text-green-400" : "bg-gray-700 text-gray-400"}`}>
                        {strategy.is_active ? "활성" : "비활성"}
                      </span>
                    </div>
                    <p className="text-sm text-gray-400 mt-1">{strategy.description ?? ""}</p>
                  </div>
                  <Link
                    href={`/strategies/${strategy.strategy_id}`}
                    className="text-sm text-blue-400 hover:text-blue-300 whitespace-nowrap"
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
                {stats ? (
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                    <StatCard label="총 실행 횟수" value={stats.total_runs} sub="회" />
                    <StatCard
                      label="승률"
                      value={stats.win_rate != null ? `${(stats.win_rate * 100).toFixed(1)}%` : "-"}
                      sub={stats.total_verified > 0 ? `${stats.success_count}승 ${stats.fail_count}패` : "검증 대기 중"}
                      color={stats.win_rate != null ? (stats.win_rate >= 0.5 ? "green" : "red") : "gray"}
                    />
                    <StatCard
                      label="평균 수익률"
                      value={stats.avg_pnl_pct != null ? `${stats.avg_pnl_pct.toFixed(2)}%` : "-"}
                      sub="검증 완료 기준"
                      color={stats.avg_pnl_pct != null ? (stats.avg_pnl_pct >= 0 ? "green" : "red") : "gray"}
                    />
                    <StatCard
                      label="기댓값"
                      value={stats.expected_value != null ? `${stats.expected_value.toFixed(2)}%` : "-"}
                      sub={`검증 ${stats.total_verified}건`}
                      color={stats.expected_value != null ? (stats.expected_value >= 0 ? "green" : "red") : "gray"}
                    />
                  </div>
                ) : (
                  <p className="text-sm text-gray-500">통계 없음</p>
                )}
              </div>
            ))}
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
                    {job.next_run
                      ? new Date(job.next_run).toLocaleString("ko-KR")
                      : "-"}
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

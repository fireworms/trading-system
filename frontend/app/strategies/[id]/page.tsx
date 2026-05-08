"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { api, RecommendationRun, StrategyStats, getToken } from "@/lib/api";
import RecommendationTable from "@/components/RecommendationTable";
import WinRateChart from "@/components/WinRateChart";
import StatCard from "@/components/StatCard";

export default function StrategyDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router  = useRouter();

  const [runs, setRuns]     = useState<RecommendationRun[]>([]);
  const [stats, setStats]   = useState<StrategyStats | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [loading, setLoading]   = useState(true);

  useEffect(() => {
    if (!getToken()) { router.push("/login"); return; }
    load();
  }, [id]); // eslint-disable-line react-hooks/exhaustive-deps

  async function load() {
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

        {/* 성과 요약 */}
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

          {/* 왼쪽: 실행 이력 + 도넛 차트 */}
          <div className="flex flex-col gap-4">
            {stats && (
              <div className="bg-gray-800 rounded-2xl p-4">
                <h3 className="text-sm font-medium text-gray-400 mb-3">검증 현황</h3>
                <WinRateChart
                  success={stats.success_count}
                  fail={stats.fail_count}
                  pending={pending}
                />
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
                        selected === run.run_id
                          ? "bg-blue-600 text-white"
                          : "text-gray-400 hover:bg-gray-700"
                      }`}
                    >
                      <div>{run.run_date}</div>
                      <div className="text-xs opacity-70">
                        {run.recommendations.length}개 · {run.ai_model_used ?? "-"}
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* 오른쪽: 추천 종목 테이블 */}
          <div className="lg:col-span-2 bg-gray-800 rounded-2xl p-6">
            {currentRun ? (
              <>
                <div className="flex items-center justify-between mb-4">
                  <h3 className="font-semibold">
                    {currentRun.run_date} 추천 종목
                  </h3>
                  <span className="text-xs text-gray-500">
                    {currentRun.prompt_version} · {currentRun.ai_model_used}
                  </span>
                </div>
                <RecommendationTable recommendations={currentRun.recommendations} />
              </>
            ) : (
              <p className="text-gray-500 text-sm">실행 이력을 선택하세요.</p>
            )}
          </div>

        </div>
      </div>
    </div>
  );
}

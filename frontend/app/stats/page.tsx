"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  api, ProfitStats, TradeKPI, getToken,
} from "@/lib/api";
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell, ReferenceLine,
} from "recharts";

function KpiCard({
  label, value, sub, color = "white",
}: { label: string; value: string | number; sub?: string; color?: string }) {
  const colorClass =
    color === "green" ? "text-green-400"
    : color === "red" ? "text-red-400"
    : "text-white";
  return (
    <div className="bg-gray-800 rounded-xl p-4">
      <p className="text-xs text-gray-400 mb-1">{label}</p>
      <p className={`text-xl font-bold ${colorClass}`}>{value}</p>
      {sub && <p className="text-xs text-gray-500 mt-0.5">{sub}</p>}
    </div>
  );
}

function fmt(n: number | null, decimals = 2, prefix = "") {
  if (n == null) return "-";
  return `${prefix}${n.toFixed(decimals)}`;
}

function fmtAmt(n: number) {
  return `${n >= 0 ? "+" : ""}${n.toLocaleString()}원`;
}

function OverallKpi({ kpi }: { kpi: TradeKPI }) {
  const winColor    = kpi.win_rate != null ? (kpi.win_rate >= 0.5 ? "green" : "red") : "white";
  const pfColor     = kpi.profit_factor != null ? (kpi.profit_factor >= 1.5 ? "green" : kpi.profit_factor >= 1 ? "white" : "red") : "white";
  const amtColor    = kpi.total_pnl_amount >= 0 ? "green" : "red";
  const sharpeColor = kpi.sharpe != null ? (kpi.sharpe >= 1 ? "green" : kpi.sharpe >= 0 ? "white" : "red") : "white";
  const mddColor    = kpi.max_drawdown_pct != null ? (kpi.max_drawdown_pct <= 5 ? "green" : kpi.max_drawdown_pct <= 15 ? "white" : "red") : "white";
  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
      <KpiCard label="총 실현손익" value={fmtAmt(kpi.total_pnl_amount)} color={amtColor} />
      <KpiCard
        label="승률"
        value={kpi.win_rate != null ? `${(kpi.win_rate * 100).toFixed(1)}%` : "-"}
        sub={`${kpi.win_count}승 ${kpi.loss_count}패 / ${kpi.total_trades}건`}
        color={winColor}
      />
      <KpiCard
        label="손익비"
        value={fmt(kpi.profit_factor)}
        sub={kpi.avg_win_pct != null && kpi.avg_loss_pct != null
          ? `평균 +${kpi.avg_win_pct.toFixed(2)}% / ${kpi.avg_loss_pct.toFixed(2)}%`
          : undefined}
        color={pfColor}
      />
      <KpiCard
        label="평균 보유기간"
        value={kpi.avg_hold_days != null ? `${kpi.avg_hold_days}일` : "-"}
        sub={`총 ${kpi.total_trades}건`}
      />
      <KpiCard
        label="샤프지수"
        value={kpi.sharpe != null ? fmt(kpi.sharpe) : "-"}
        sub="트레이드 단위 (3건+)"
        color={sharpeColor}
      />
      <KpiCard
        label="최대낙폭 (MDD)"
        value={kpi.max_drawdown_pct != null ? `-${kpi.max_drawdown_pct.toFixed(2)}%` : "-"}
        sub="equity curve 기준"
        color={mddColor}
      />
    </div>
  );
}

export default function StatsPage() {
  const router = useRouter();
  const [stats, setStats] = useState<ProfitStats | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!getToken()) { router.push("/login"); return; }
    api.positions.stats()
      .then(setStats)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  if (loading) return (
    <div className="min-h-screen bg-gray-900 flex items-center justify-center text-gray-400">로딩 중...</div>
  );

  if (!stats || stats.overall.total_trades === 0) return (
    <div className="min-h-screen bg-gray-900 text-white p-6">
      <div className="max-w-5xl mx-auto">
        <h1 className="text-xl font-bold mb-6">수익통계</h1>
        <p className="text-gray-500">확정된 거래가 없습니다. 첫 번째 포지션이 청산되면 통계가 표시됩니다.</p>
      </div>
    </div>
  );

  const { overall, by_strategy, by_month, by_stock, trades } = stats;

  return (
    <div className="min-h-screen bg-gray-900 text-white p-6">
      <div className="max-w-5xl mx-auto flex flex-col gap-8">
        <h1 className="text-xl font-bold">수익통계</h1>

        {/* 전체 KPI */}
        <section>
          <h2 className="text-sm font-semibold text-gray-400 mb-3">전체</h2>
          <OverallKpi kpi={overall} />
        </section>

        {/* 전략별 */}
        <section>
          <h2 className="text-sm font-semibold text-gray-400 mb-3">전략별</h2>
          <div className="bg-gray-800 rounded-xl overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs text-gray-400 border-b border-gray-700">
                  <th className="p-3 text-left">전략</th>
                  <th className="p-3 text-right">거래수</th>
                  <th className="p-3 text-right">승/패</th>
                  <th className="p-3 text-right">승률</th>
                  <th className="p-3 text-right">평균수익</th>
                  <th className="p-3 text-right">평균손실</th>
                  <th className="p-3 text-right">손익비</th>
                  <th className="p-3 text-right">총손익</th>
                </tr>
              </thead>
              <tbody>
                {by_strategy.map((s) => (
                  <tr key={s.strategy_id} className="border-b border-gray-700/50 hover:bg-gray-700/30">
                    <td className="p-3 font-medium">{s.strategy_name}</td>
                    <td className="p-3 text-right text-gray-300">{s.total_trades}</td>
                    <td className="p-3 text-right text-gray-300">{s.win_count}/{s.loss_count}</td>
                    <td className={`p-3 text-right font-medium ${s.win_rate != null && s.win_rate >= 0.5 ? "text-green-400" : "text-red-400"}`}>
                      {s.win_rate != null ? `${(s.win_rate * 100).toFixed(1)}%` : "-"}
                    </td>
                    <td className="p-3 text-right text-green-400">{fmt(s.avg_win_pct, 2, "+")}</td>
                    <td className="p-3 text-right text-red-400">{fmt(s.avg_loss_pct, 2)}</td>
                    <td className={`p-3 text-right ${s.profit_factor != null && s.profit_factor >= 1.5 ? "text-green-400" : "text-gray-300"}`}>
                      {fmt(s.profit_factor)}
                    </td>
                    <td className={`p-3 text-right font-bold ${s.total_pnl_amount >= 0 ? "text-green-400" : "text-red-400"}`}>
                      {fmtAmt(s.total_pnl_amount)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        {/* 월별 손익 차트 */}
        <section>
          <h2 className="text-sm font-semibold text-gray-400 mb-3">월별 손익</h2>
          <div className="bg-gray-800 rounded-xl p-4">
            {by_month.length === 0 ? (
              <p className="text-gray-500 text-sm">데이터 없음</p>
            ) : (
              <ResponsiveContainer width="100%" height={200}>
                <BarChart data={by_month} margin={{ top: 4, right: 8, left: 8, bottom: 4 }}>
                  <XAxis dataKey="month" tick={{ fill: "#9ca3af", fontSize: 11 }} />
                  <YAxis tick={{ fill: "#9ca3af", fontSize: 11 }} tickFormatter={(v) => `${v.toLocaleString()}원`} width={80} />
                  <Tooltip
                    contentStyle={{ background: "#1f2937", border: "none", borderRadius: 8 }}
                    formatter={(v: unknown) => [`${Number(v).toLocaleString()}원`, "손익"]}
                  />
                  <ReferenceLine y={0} stroke="#4b5563" />
                  <Bar dataKey="total_pnl_amount" radius={[4, 4, 0, 0]}>
                    {by_month.map((m) => (
                      <Cell key={m.month} fill={m.total_pnl_amount >= 0 ? "#34d399" : "#f87171"} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            )}
          </div>
        </section>

        {/* 종목별 */}
        <section>
          <h2 className="text-sm font-semibold text-gray-400 mb-3">종목별</h2>
          <div className="bg-gray-800 rounded-xl overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs text-gray-400 border-b border-gray-700">
                  <th className="p-3 text-left">종목</th>
                  <th className="p-3 text-right">거래수</th>
                  <th className="p-3 text-right">승/패</th>
                  <th className="p-3 text-right">평균손익</th>
                  <th className="p-3 text-right">총손익</th>
                </tr>
              </thead>
              <tbody>
                {by_stock.map((s) => (
                  <tr key={s.stock_code} className="border-b border-gray-700/50 hover:bg-gray-700/30">
                    <td className="p-3 font-medium">
                      {s.stock_name || s.stock_code}
                      <span className="ml-1.5 text-xs text-gray-500">{s.stock_name ? s.stock_code : ""}</span>
                    </td>
                    <td className="p-3 text-right text-gray-300">{s.total_trades}</td>
                    <td className="p-3 text-right text-gray-300">{s.win_count}/{s.total_trades - s.win_count}</td>
                    <td className={`p-3 text-right font-medium ${s.avg_pnl_pct >= 0 ? "text-green-400" : "text-red-400"}`}>
                      {s.avg_pnl_pct >= 0 ? "+" : ""}{s.avg_pnl_pct.toFixed(2)}%
                    </td>
                    <td className={`p-3 text-right font-bold ${s.total_pnl_amount >= 0 ? "text-green-400" : "text-red-400"}`}>
                      {fmtAmt(s.total_pnl_amount)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        {/* 거래 목록 */}
        <section>
          <h2 className="text-sm font-semibold text-gray-400 mb-3">거래 목록</h2>
          <div className="bg-gray-800 rounded-xl overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs text-gray-400 border-b border-gray-700">
                  <th className="p-3 text-left">청산일</th>
                  <th className="p-3 text-left">종목</th>
                  <th className="p-3 text-left">전략</th>
                  <th className="p-3 text-right">수량</th>
                  <th className="p-3 text-right">진입가</th>
                  <th className="p-3 text-right">청산가</th>
                  <th className="p-3 text-right">보유일</th>
                  <th className="p-3 text-right">손익%</th>
                  <th className="p-3 text-right">손익금액</th>
                  <th className="p-3 text-right">구분</th>
                </tr>
              </thead>
              <tbody>
                {trades.map((t) => (
                  <tr key={t.position_id} className="border-b border-gray-700/50 hover:bg-gray-700/30">
                    <td className="p-3 text-gray-400 text-xs">{t.exit_date ?? "-"}</td>
                    <td className="p-3 font-medium">
                      {t.stock_name || t.stock_code}
                      <span className="ml-1.5 text-xs text-gray-500">{t.stock_name ? t.stock_code : ""}</span>
                    </td>
                    <td className="p-3 text-gray-400 text-xs">{t.strategy_name}</td>
                    <td className="p-3 text-right text-gray-300">{t.quantity}</td>
                    <td className="p-3 text-right text-gray-300">{t.entry_price.toLocaleString()}</td>
                    <td className="p-3 text-right text-gray-300">{t.exit_price.toLocaleString()}</td>
                    <td className="p-3 text-right text-gray-400">{t.hold_days ?? "-"}일</td>
                    <td className={`p-3 text-right font-bold ${t.pnl_pct >= 0 ? "text-green-400" : "text-red-400"}`}>
                      {t.pnl_pct >= 0 ? "+" : ""}{t.pnl_pct.toFixed(2)}%
                    </td>
                    <td className={`p-3 text-right font-bold ${t.pnl_amount >= 0 ? "text-green-400" : "text-red-400"}`}>
                      {fmtAmt(t.pnl_amount)}
                    </td>
                    <td className="p-3 text-right text-xs text-gray-500">{t.status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </div>
  );
}

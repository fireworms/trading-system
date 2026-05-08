"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, Position, PositionStatus, getToken } from "@/lib/api";
import Badge from "@/components/Badge";

const STATUS_TABS: { value: PositionStatus | "ALL"; label: string }[] = [
  { value: "ALL",         label: "전체" },
  { value: "HOLDING",     label: "보유중" },
  { value: "TARGET_HIT",  label: "목표달성" },
  { value: "STOP_LOSS",   label: "손절" },
  { value: "EXPIRED",     label: "만료" },
];

function pnlColor(pnl: string | null) {
  if (!pnl) return "text-gray-400";
  return parseFloat(pnl) >= 0 ? "text-green-400" : "text-red-400";
}

export default function PositionsPage() {
  const router = useRouter();
  const [positions, setPositions] = useState<Position[]>([]);
  const [tab, setTab]             = useState<PositionStatus | "ALL">("ALL");
  const [loading, setLoading]     = useState(true);

  useEffect(() => {
    if (!getToken()) { router.push("/login"); return; }
    load();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function load() {
    try {
      const data = await api.positions.list();
      setPositions(data);
    } finally {
      setLoading(false);
    }
  }

  const filtered = tab === "ALL" ? positions : positions.filter((p) => p.status === tab);

  const holding    = positions.filter((p) => p.status === "HOLDING");
  const closed     = positions.filter((p) => p.status !== "HOLDING");
  const winCount   = closed.filter((p) => p.status === "TARGET_HIT").length;
  const winRate    = closed.length > 0 ? ((winCount / closed.length) * 100).toFixed(1) : null;
  const avgPnl     = closed.length > 0
    ? (closed.reduce((s, p) => s + parseFloat(p.pnl_pct ?? "0"), 0) / closed.length).toFixed(2)
    : null;

  if (loading) return (
    <div className="flex items-center justify-center h-64 text-gray-400">로딩 중...</div>
  );

  return (
    <div className="max-w-5xl mx-auto p-6">
      <h1 className="text-2xl font-bold mb-6">포지션 현황</h1>

      {/* 요약 */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-6">
        <div className="bg-gray-800 rounded-xl p-4">
          <div className="text-xs text-gray-400">보유중</div>
          <div className="text-2xl font-bold text-blue-400">{holding.length}</div>
        </div>
        <div className="bg-gray-800 rounded-xl p-4">
          <div className="text-xs text-gray-400">종료 포지션</div>
          <div className="text-2xl font-bold text-gray-300">{closed.length}</div>
        </div>
        <div className="bg-gray-800 rounded-xl p-4">
          <div className="text-xs text-gray-400">승률</div>
          <div className={`text-2xl font-bold ${winRate && parseFloat(winRate) >= 50 ? "text-green-400" : "text-gray-400"}`}>
            {winRate ? `${winRate}%` : "-"}
          </div>
          {closed.length > 0 && <div className="text-xs text-gray-500">{winCount}승 {closed.length - winCount}패</div>}
        </div>
        <div className="bg-gray-800 rounded-xl p-4">
          <div className="text-xs text-gray-400">평균 수익률</div>
          <div className={`text-2xl font-bold ${avgPnl && parseFloat(avgPnl) >= 0 ? "text-green-400" : "text-red-400"}`}>
            {avgPnl ? `${parseFloat(avgPnl) >= 0 ? "+" : ""}${avgPnl}%` : "-"}
          </div>
        </div>
      </div>

      {/* 탭 */}
      <div className="flex gap-1 mb-4">
        {STATUS_TABS.map((t) => (
          <button key={t.value} onClick={() => setTab(t.value)}
            className={`px-3 py-1.5 rounded-lg text-sm transition-colors ${
              tab === t.value ? "bg-blue-600 text-white" : "text-gray-400 hover:bg-gray-700"
            }`}>
            {t.label}
            {t.value !== "ALL" && (
              <span className="ml-1 text-xs opacity-70">
                {positions.filter((p) => p.status === t.value).length}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* 테이블 */}
      {filtered.length === 0 ? (
        <p className="text-gray-500 text-center py-12">포지션 없음</p>
      ) : (
        <div className="bg-gray-800 rounded-2xl overflow-hidden">
          <table className="w-full text-sm">
            <thead className="text-gray-400 border-b border-gray-700">
              <tr>
                <th className="text-left p-4">종목</th>
                <th className="text-left p-4">상태</th>
                <th className="text-right p-4">수량</th>
                <th className="text-right p-4">매수가</th>
                <th className="text-right p-4">매도가</th>
                <th className="text-right p-4">수익률</th>
                <th className="text-right p-4">매수일</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((pos) => (
                <tr key={pos.position_id} className="border-b border-gray-700/50 hover:bg-gray-700/30">
                  <td className="p-4 font-medium">{pos.stock_code}</td>
                  <td className="p-4"><Badge value={pos.status} /></td>
                  <td className="p-4 text-right text-gray-300">{pos.quantity}</td>
                  <td className="p-4 text-right text-gray-300">
                    {Number(pos.entry_price).toLocaleString()}
                  </td>
                  <td className="p-4 text-right text-gray-300">
                    {pos.exit_price ? Number(pos.exit_price).toLocaleString() : "-"}
                  </td>
                  <td className={`p-4 text-right font-bold ${pnlColor(pos.pnl_pct)}`}>
                    {pos.pnl_pct
                      ? `${parseFloat(pos.pnl_pct) >= 0 ? "+" : ""}${parseFloat(pos.pnl_pct).toFixed(2)}%`
                      : "-"}
                  </td>
                  <td className="p-4 text-right text-gray-400 text-xs">{pos.entry_date}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

"use client";

import { Recommendation } from "@/lib/api";

interface Props {
  recommendations: Recommendation[];
}

function probColor(p: number) {
  if (p >= 75) return "text-green-400";
  if (p >= 60) return "text-yellow-400";
  return "text-gray-400";
}

function VerificationBadge({ v }: { v: Recommendation["verification"] }) {
  if (!v) return <span className="text-xs text-gray-600">미검증</span>;
  const pnl = v.pnl_pct ? parseFloat(v.pnl_pct) : null;
  const sign = pnl != null && pnl >= 0 ? "+" : "";
  return (
    <div className="flex flex-col items-end gap-0.5">
      <span className={`text-xs font-bold px-1.5 py-0.5 rounded ${
        v.result === "SUCCESS" ? "bg-green-900 text-green-300" : "bg-red-900 text-red-300"
      }`}>
        {v.result === "SUCCESS" ? "✓ 성공" : "✗ 실패"}
      </span>
      {pnl != null && (
        <span className={`text-xs ${pnl >= 0 ? "text-red-400" : "text-blue-400"}`}>
          {sign}{pnl.toFixed(2)}%
        </span>
      )}
    </div>
  );
}

export default function RecommendationTable({ recommendations }: Props) {
  if (!recommendations.length)
    return <p className="text-gray-500 text-sm">추천 종목 없음</p>;

  const hasVerification = recommendations.some((r) => r.verification);

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-gray-400 border-b border-gray-700">
            <th className="pb-2 text-left">순위</th>
            <th className="pb-2 text-left">종목</th>
            <th className="pb-2 text-right">확률</th>
            <th className="pb-2 text-right">목표가</th>
            <th className="pb-2 text-right">손절가</th>
            {hasVerification && <th className="pb-2 text-right">검증</th>}
            <th className="pb-2 text-left pl-4">근거</th>
          </tr>
        </thead>
        <tbody>
          {recommendations.map((rec) => {
            const prob = rec.ai_probability ? parseFloat(rec.ai_probability) : null;
            return (
              <tr key={rec.rec_id} className="border-b border-gray-800 hover:bg-gray-800/50">
                <td className="py-2 text-gray-400">{rec.rank ?? "-"}</td>
                <td className="py-2">
                  <div className="font-medium">{rec.stock_name}</div>
                  <div className="text-xs text-gray-500">{rec.stock_code}</div>
                </td>
                <td className={`py-2 text-right font-bold ${prob ? probColor(prob) : "text-gray-400"}`}>
                  {prob != null ? `${prob.toFixed(1)}%` : "-"}
                </td>
                <td className="py-2 text-right text-red-400">
                  {rec.target_price ? Number(rec.target_price).toLocaleString() : "-"}
                </td>
                <td className="py-2 text-right text-blue-400">
                  {rec.stop_loss_price ? Number(rec.stop_loss_price).toLocaleString() : "-"}
                </td>
                {hasVerification && (
                  <td className="py-2 text-right">
                    <VerificationBadge v={rec.verification} />
                  </td>
                )}
                <td className="py-2 pl-4 text-gray-400 max-w-xs truncate">
                  {rec.ai_reason?.slice(0, 60) ?? "-"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

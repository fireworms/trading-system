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

export default function RecommendationTable({ recommendations }: Props) {
  if (!recommendations.length)
    return <p className="text-gray-500 text-sm">추천 종목 없음</p>;

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
                <td className="py-2 text-right text-blue-400">
                  {rec.target_price ? Number(rec.target_price).toLocaleString() : "-"}
                </td>
                <td className="py-2 text-right text-red-400">
                  {rec.stop_loss_price ? Number(rec.stop_loss_price).toLocaleString() : "-"}
                </td>
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

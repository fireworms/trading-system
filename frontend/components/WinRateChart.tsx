"use client";

import { PieChart, Pie, Cell, Tooltip, Legend, ResponsiveContainer } from "recharts";

interface Props {
  success: number;
  fail: number;
  pending: number;
}

const COLORS = ["#22c55e", "#ef4444", "#6b7280"];

export default function WinRateChart({ success, fail, pending }: Props) {
  const data = [
    { name: "성공", value: success },
    { name: "실패", value: fail },
    { name: "검증 전", value: pending },
  ].filter((d) => d.value > 0);

  if (data.length === 0)
    return <p className="text-gray-500 text-sm">검증 데이터 없음</p>;

  return (
    <ResponsiveContainer width="100%" height={200}>
      <PieChart>
        <Pie data={data} cx="50%" cy="50%" innerRadius={50} outerRadius={80} dataKey="value">
          {data.map((_, i) => (
            <Cell key={i} fill={COLORS[i % COLORS.length]} />
          ))}
        </Pie>
        <Tooltip
          contentStyle={{ background: "#1f2937", border: "none", borderRadius: 8 }}
          labelStyle={{ color: "#9ca3af" }}
        />
        <Legend />
      </PieChart>
    </ResponsiveContainer>
  );
}

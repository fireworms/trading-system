interface Props {
  label: string;
  value: string | number;
  sub?: string;
  color?: "green" | "red" | "blue" | "gray";
}

const colors = {
  green: "text-green-400",
  red:   "text-red-400",
  blue:  "text-blue-400",
  gray:  "text-gray-400",
};

export default function StatCard({ label, value, sub, color = "gray" }: Props) {
  return (
    <div className="bg-gray-800 rounded-xl p-4 flex flex-col gap-1">
      <span className="text-xs text-gray-400">{label}</span>
      <span className={`text-2xl font-bold ${colors[color]}`}>{value}</span>
      {sub && <span className="text-xs text-gray-500">{sub}</span>}
    </div>
  );
}

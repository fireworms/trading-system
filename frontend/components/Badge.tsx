const styles: Record<string, string> = {
  HOLDING:     "bg-blue-900 text-blue-300",
  TARGET_HIT:  "bg-green-900 text-green-300",
  STOP_LOSS:   "bg-red-900 text-red-300",
  EXPIRED:     "bg-gray-700 text-gray-300",
  MANUAL_EXIT: "bg-yellow-900 text-yellow-300",
  ACTIVE:      "bg-green-900 text-green-300",
  INACTIVE:    "bg-gray-700 text-gray-300",
  SUPER_ADMIN: "bg-purple-900 text-purple-300",
  ADMIN:       "bg-blue-900 text-blue-300",
  TRADER:      "bg-teal-900 text-teal-300",
  VIEWER:      "bg-gray-700 text-gray-300",
};

const labels: Record<string, string> = {
  HOLDING: "보유중", TARGET_HIT: "목표달성", STOP_LOSS: "손절",
  EXPIRED: "만료", MANUAL_EXIT: "수동종료",
};

export default function Badge({ value }: { value: string }) {
  return (
    <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${styles[value] ?? "bg-gray-700 text-gray-300"}`}>
      {labels[value] ?? value}
    </span>
  );
}

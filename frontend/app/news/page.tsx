"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, getToken, NewsEvent } from "@/lib/api";

const SEVERITY_STYLE: Record<string, string> = {
  CRITICAL: "bg-red-900/60 text-red-300 border border-red-700",
  WARNING:  "bg-yellow-900/60 text-yellow-300 border border-yellow-700",
  NORMAL:   "bg-gray-700/60 text-gray-300 border border-gray-600",
};

const SEVERITY_DOT: Record<string, string> = {
  CRITICAL: "bg-red-400",
  WARNING:  "bg-yellow-400",
  NORMAL:   "bg-gray-400",
};

function ChangeCell({ v }: { v: number | null }) {
  if (v == null) return <span className="text-gray-600">-</span>;
  return (
    <span className={v >= 0 ? "text-red-400" : "text-blue-400"}>
      {v >= 0 ? "+" : ""}{v.toFixed(2)}%
    </span>
  );
}

export default function NewsPage() {
  const router = useRouter();
  const [events, setEvents] = useState<NewsEvent[]>([]);
  const [filter, setFilter] = useState<string>("ALL");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!getToken()) { router.push("/login"); return; }
    api.newsEvents.list().then(setEvents).finally(() => setLoading(false));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const filtered = filter === "ALL" ? events : events.filter(e => e.severity === filter);

  if (loading) return (
    <div className="min-h-screen bg-gray-900 flex items-center justify-center text-gray-400">로딩 중...</div>
  );

  return (
    <div className="min-h-screen bg-gray-900 text-white p-6">
      <div className="max-w-4xl mx-auto flex flex-col gap-6">
        <div className="flex items-center justify-between">
          <h1 className="text-xl font-bold">뉴스 감시 히스토리</h1>
          <span className="text-sm text-gray-400">{events.length}건</span>
        </div>

        {/* 필터 */}
        <div className="flex gap-2">
          {["ALL", "CRITICAL", "WARNING", "NORMAL"].map(s => (
            <button
              key={s}
              onClick={() => setFilter(s)}
              className={`px-3 py-1.5 rounded-lg text-sm transition-colors ${
                filter === s ? "bg-blue-600 text-white" : "bg-gray-800 text-gray-400 hover:bg-gray-700"
              }`}
            >
              {s === "ALL" ? "전체" : s}
            </button>
          ))}
        </div>

        {filtered.length === 0 ? (
          <p className="text-gray-500">이벤트가 없습니다.</p>
        ) : (
          <div className="flex flex-col gap-3">
            {filtered.map(e => (
              <div key={e.event_id} className="bg-gray-800 rounded-xl p-4 flex flex-col gap-3">
                {/* 헤더 */}
                <div className="flex items-start justify-between gap-3">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className={`flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full font-medium ${SEVERITY_STYLE[e.severity]}`}>
                      <span className={`w-1.5 h-1.5 rounded-full ${SEVERITY_DOT[e.severity]}`} />
                      {e.severity}
                    </span>
                    {e.ai_confidence != null && (
                      <span className="text-xs text-gray-400 bg-gray-700 px-2 py-1 rounded-full">
                        확신도 {(e.ai_confidence * 100).toFixed(0)}%
                      </span>
                    )}
                  </div>
                  <span className="text-xs text-gray-500 shrink-0">
                    {new Date(e.detected_at).toLocaleString("ko-KR", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })}
                  </span>
                </div>

                {/* 내용 */}
                <p className="text-sm text-gray-200 leading-relaxed">{e.event_description}</p>

                {/* 키워드 */}
                {e.keywords.length > 0 && (
                  <div className="flex flex-wrap gap-1.5">
                    {e.keywords.map((k, i) => (
                      <span key={i} className="text-xs bg-gray-700 text-gray-300 px-2 py-0.5 rounded">
                        {k}
                      </span>
                    ))}
                  </div>
                )}

                {/* 지수 & 사후 영향 */}
                {(e.kospi_at_detection || e.verified_1d || e.verified_3d) && (
                  <div className="border-t border-gray-700 pt-3 grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
                    {e.kospi_at_detection && (
                      <div>
                        <p className="text-gray-500 mb-0.5">감지 시점 KOSPI</p>
                        <p className="text-gray-200">{e.kospi_at_detection.toLocaleString()}</p>
                      </div>
                    )}
                    {e.kosdaq_at_detection && (
                      <div>
                        <p className="text-gray-500 mb-0.5">감지 시점 KOSDAQ</p>
                        <p className="text-gray-200">{e.kosdaq_at_detection.toLocaleString()}</p>
                      </div>
                    )}
                    {e.verified_1d && (
                      <div>
                        <p className="text-gray-500 mb-0.5">1일 후 KOSPI/KOSDAQ</p>
                        <p><ChangeCell v={e.kospi_change_1d} /> / <ChangeCell v={e.kosdaq_change_1d} /></p>
                      </div>
                    )}
                    {e.verified_3d && (
                      <div>
                        <p className="text-gray-500 mb-0.5">3일 후 KOSPI/KOSDAQ</p>
                        <p><ChangeCell v={e.kospi_change_3d} /> / <ChangeCell v={e.kosdaq_change_3d} /></p>
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

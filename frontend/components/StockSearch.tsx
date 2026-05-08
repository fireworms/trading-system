"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { api } from "@/lib/api";
import { StockItem } from "@/lib/korean-stocks";

interface Props {
  onSelect: (stock: StockItem) => void;
  placeholder?: string;
  market?: string;
}

export default function StockSearch({
  onSelect,
  placeholder = "종목명 또는 코드 검색",
  market,
}: Props) {
  const [query,   setQuery]   = useState("");
  const [results, setResults] = useState<StockItem[]>([]);
  const [open,    setOpen]    = useState(false);
  const [focused, setFocused] = useState(0);
  const [loading, setLoading] = useState(false);
  const ref    = useRef<HTMLDivElement>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // 디바운스 API 검색
  const search = useCallback(
    (q: string) => {
      if (timerRef.current) clearTimeout(timerRef.current);
      if (!q.trim()) { setResults([]); setOpen(false); return; }

      timerRef.current = setTimeout(async () => {
        setLoading(true);
        try {
          const items = await api.stockMaster.search(q.trim(), market);
          setResults(
            items.map((s) => ({
              code:    s.stock_code,
              name:    s.stock_name,
              market:  s.market,
              country: s.country,
              sector:  s.sector,
            }))
          );
          setFocused(0);
          setOpen(true);
        } catch {
          setResults([]);
        } finally {
          setLoading(false);
        }
      }, 200);
    },
    [market]
  );

  useEffect(() => { search(query); }, [query, search]);

  // 외부 클릭 닫기
  useEffect(() => {
    function handler(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  function handleKey(e: React.KeyboardEvent) {
    if (!open || results.length === 0) return;
    if (e.key === "ArrowDown") { e.preventDefault(); setFocused((f) => Math.min(f + 1, results.length - 1)); }
    if (e.key === "ArrowUp")   { e.preventDefault(); setFocused((f) => Math.max(f - 1, 0)); }
    if (e.key === "Enter")     { e.preventDefault(); select(results[focused]); }
    if (e.key === "Escape")    setOpen(false);
  }

  function select(stock: StockItem) {
    onSelect(stock);
    setQuery("");
    setOpen(false);
    setResults([]);
  }

  const marketBadge = (m: string) => {
    if (m === "KOSPI")  return "text-blue-400";
    if (m === "KOSDAQ") return "text-green-400";
    if (m === "NAS")    return "text-purple-400";
    return "text-gray-400";
  };

  return (
    <div ref={ref} className="relative">
      <input
        type="text"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={handleKey}
        onFocus={() => query.trim() && setOpen(true)}
        placeholder={placeholder}
        className="w-full bg-gray-700 text-white rounded-lg px-4 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-500"
        autoComplete="off"
      />
      {loading && (
        <span className="absolute right-3 top-2.5 text-xs text-gray-400">검색 중...</span>
      )}

      {open && results.length > 0 && (
        <ul className="absolute z-50 top-full mt-1 w-full bg-gray-800 border border-gray-600 rounded-lg shadow-xl overflow-hidden max-h-72 overflow-y-auto">
          {results.map((s, i) => (
            <li
              key={`${s.code}-${s.market}`}
              onMouseDown={() => select(s)}
              className={`flex items-center justify-between px-4 py-2.5 cursor-pointer text-sm ${
                i === focused ? "bg-blue-600" : "hover:bg-gray-700"
              }`}
            >
              <div>
                <span className="font-medium">{s.name}</span>
                <span className="ml-2 text-xs text-gray-400 font-mono">{s.code}</span>
              </div>
              <div className="flex items-center gap-2 text-xs">
                <span className={marketBadge(s.market)}>{s.market}</span>
                {s.sector && <span className="text-gray-500">{s.sector}</span>}
              </div>
            </li>
          ))}
        </ul>
      )}

      {open && !loading && query.trim().length > 0 && results.length === 0 && (
        <div className="absolute z-50 top-full mt-1 w-full bg-gray-800 border border-gray-600 rounded-lg px-4 py-3 text-sm text-gray-400">
          검색 결과 없음
          {!query.trim().includes(" ") && (
            <span className="ml-1 text-gray-500">— stock_master가 비어있다면 관리자에게 업데이트 요청하세요</span>
          )}
        </div>
      )}
    </div>
  );
}

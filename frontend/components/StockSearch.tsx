"use client";

import { useState, useRef, useEffect } from "react";
import { searchStocks, StockItem } from "@/lib/korean-stocks";

interface Props {
  onSelect: (stock: StockItem) => void;
  placeholder?: string;
}

export default function StockSearch({ onSelect, placeholder = "종목명 또는 코드 검색" }: Props) {
  const [query, setQuery]       = useState("");
  const [results, setResults]   = useState<StockItem[]>([]);
  const [open, setOpen]         = useState(false);
  const [focused, setFocused]   = useState(0);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setResults(searchStocks(query));
    setFocused(0);
    setOpen(query.trim().length > 0);
  }, [query]);

  // 외부 클릭 시 닫기
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
  }

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
      {open && results.length > 0 && (
        <ul className="absolute z-50 top-full mt-1 w-full bg-gray-800 border border-gray-600 rounded-lg shadow-xl overflow-hidden">
          {results.map((s, i) => (
            <li key={s.code}
              onMouseDown={() => select(s)}
              className={`flex items-center justify-between px-4 py-2.5 cursor-pointer text-sm ${
                i === focused ? "bg-blue-600" : "hover:bg-gray-700"
              }`}>
              <div>
                <span className="font-medium">{s.name}</span>
                <span className="ml-2 text-xs text-gray-400 font-mono">{s.code}</span>
              </div>
              <div className="flex items-center gap-2 text-xs text-gray-400">
                <span>{s.market}</span>
                <span>{s.sector}</span>
              </div>
            </li>
          ))}
          {query.trim().length === 6 && /^\d{6}$/.test(query.trim()) && results.length === 0 && (
            <li
              onMouseDown={() => select({ code: query.trim(), name: query.trim(), market: "KOSPI", sector: "" })}
              className="px-4 py-2.5 cursor-pointer text-sm hover:bg-gray-700 text-gray-300">
              <span className="font-mono">{query.trim()}</span>
              <span className="ml-2 text-gray-500">직접 입력 (목록에 없는 종목)</span>
            </li>
          )}
        </ul>
      )}
      {open && query.trim().length > 0 && results.length === 0 && !/^\d{6}$/.test(query.trim()) && (
        <div className="absolute z-50 top-full mt-1 w-full bg-gray-800 border border-gray-600 rounded-lg px-4 py-3 text-sm text-gray-400">
          검색 결과 없음 — 6자리 코드를 직접 입력하세요
        </div>
      )}
    </div>
  );
}

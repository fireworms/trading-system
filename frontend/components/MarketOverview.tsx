"use client";

import { useEffect, useState, useRef } from "react";
import { api, MarketOverview, StockSnap } from "@/lib/api";
import { usePriceStream, LivePrice } from "@/hooks/usePriceStream";

// ── helpers ──────────────────────────────────────────────────────────

function pctColor(pct: number): string {
  if (pct > 0) return "text-red-400";    // 한국 관례: 상승=빨강
  if (pct < 0) return "text-blue-400";   // 하락=파랑
  return "text-gray-500";
}

function fmtPct(pct: number): string {
  return `${pct > 0 ? "+" : ""}${pct.toFixed(2)}%`;
}

function fmtPrice(price: number, isUsd: boolean): string {
  if (isUsd) return `$${price.toFixed(2)}`;
  return price.toLocaleString("ko-KR");
}

// ── 지수 배지 ─────────────────────────────────────────────────────────

function IndexBadge({
  label, level, change_pct, labelColor, isUsd = false,
}: {
  label: string; level: number; change_pct: number; labelColor: string; isUsd?: boolean;
}) {
  return (
    <div className="flex items-center justify-between mb-2">
      <span className={`text-sm font-semibold ${labelColor}`}>{label}</span>
      <div className="flex items-baseline gap-2">
        <span className="text-base font-bold text-white">
          {isUsd
            ? `$${level.toFixed(2)}`
            : level.toLocaleString("ko-KR", { maximumFractionDigits: 2 })}
        </span>
        <span className={`text-xs font-semibold ${pctColor(change_pct)}`}>
          {fmtPct(change_pct)}
        </span>
      </div>
    </div>
  );
}

// ── 종목 슬라이드 ─────────────────────────────────────────────────────

function StockSlide({
  stocks, livePrices, visible, isUsd = false,
}: {
  stocks: StockSnap[];
  livePrices: Record<string, LivePrice>;
  visible: boolean;
  isUsd?: boolean;
}) {
  return (
    <div className={`transition-opacity duration-500 ${visible ? "opacity-100" : "opacity-0"}`}>
      {stocks.map((s) => {
        const live  = !isUsd ? livePrices[s.code] : undefined;
        const price = live ? live.current_price : s.price;  // 현재가만 실시간 오버레이
        const pct   = s.change_pct;                         // 등락률은 REST 기준 (WebSocket PRDY_CTRT 오류 방어)
        return (
          <div key={s.code} className="flex items-center justify-between py-[3px]">
            <div className="flex items-center gap-1.5 min-w-0">
              <span className="text-[10px] text-gray-600 font-mono shrink-0 hidden sm:block">
                {s.code}
              </span>
              <span className="text-xs text-gray-300 truncate">{s.name}</span>
            </div>
            <div className="flex items-center gap-2 shrink-0 ml-2">
              <span className="text-xs font-medium text-white tabular-nums">
                {fmtPrice(price, isUsd)}
              </span>
              <span className={`text-[11px] w-14 text-right tabular-nums ${pctColor(pct)}`}>
                {fmtPct(pct)}
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ── 로딩 스켈레톤 ─────────────────────────────────────────────────────

function Skeleton() {
  return (
    <div className="sticky top-0 z-20 bg-gray-900 pb-4">
      <div className="bg-gray-800 rounded-2xl p-4 animate-pulse">
        <div className="h-3 bg-gray-700 rounded w-20 mb-4" />
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          {[0, 1, 2].map((i) => (
            <div key={i}>
              <div className="flex justify-between mb-3">
                <div className="h-4 bg-gray-700 rounded w-16" />
                <div className="h-4 bg-gray-700 rounded w-24" />
              </div>
              <div className="border-t border-gray-700 pt-2 space-y-2">
                {[0, 1, 2, 3].map((j) => (
                  <div key={j} className="flex justify-between">
                    <div className="h-3 bg-gray-700 rounded w-20" />
                    <div className="h-3 bg-gray-700 rounded w-16" />
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ── 메인 컴포넌트 ─────────────────────────────────────────────────────

const KOSPI_PAGE_SIZE  = 4;
const KOSDAQ_PAGE_SIZE = 3;
const NAS_PAGE_SIZE    = 4;
const VISIBLE_MS = 3500;
const FADE_MS    = 500;
const CYCLE_MS   = VISIBLE_MS + FADE_MS;

export default function MarketOverviewPanel() {
  const [data, setData]       = useState<MarketOverview | null>(null);
  const [pageIdx, setPageIdx] = useState(0);
  const [visible, setVisible] = useState(true);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // 초기 로드 + 60s 갱신
  useEffect(() => {
    let active = true;
    async function load() {
      try {
        const d = await api.market.overview();
        if (active) setData(d);
      } catch { /* 시장 현황은 비필수 — 조용히 처리 */ }
    }
    load();
    const refresh = setInterval(load, 60_000);
    return () => { active = false; clearInterval(refresh); };
  }, []);

  // 캐러셀: fade-out → 페이지 전환 → fade-in
  useEffect(() => {
    if (!data) return;
    timerRef.current = setInterval(() => {
      setVisible(false);
      setTimeout(() => {
        setPageIdx((p) => p + 1);
        setVisible(true);
      }, FADE_MS);
    }, CYCLE_MS);
    return () => { if (timerRef.current) clearInterval(timerRef.current); };
  }, [data]);

  // 국내주 실시간 가격 스트림
  const krCodes = data
    ? [...data.kospi_stocks.map((s) => s.code), ...data.kosdaq_stocks.map((s) => s.code)]
    : [];
  const { prices: livePrices } = usePriceStream(krCodes);

  if (!data) return <Skeleton />;

  const p = pageIdx % 2;
  const kospiSlice  = (data.kospi_stocks  ?? []).slice(p * KOSPI_PAGE_SIZE,  (p + 1) * KOSPI_PAGE_SIZE);
  const kosdaqSlice = (data.kosdaq_stocks ?? []).slice(p * KOSDAQ_PAGE_SIZE, (p + 1) * KOSDAQ_PAGE_SIZE);
  const nasSlice    = (data.nasdaq_stocks ?? []).slice(p * NAS_PAGE_SIZE,    (p + 1) * NAS_PAGE_SIZE);

  const updatedAt = new Date(data.cached_at * 1000).toLocaleTimeString("ko-KR", {
    hour: "2-digit", minute: "2-digit",
  });

  return (
    <div className="sticky top-0 z-20 bg-gray-900 pb-4">
      <div className="bg-gray-800 rounded-2xl p-4">

        {/* 헤더 */}
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-xs font-semibold text-gray-400 uppercase tracking-wide">
            시장 현황
          </h2>
          <span className="text-xs text-gray-600">{updatedAt} 기준</span>
        </div>

        {/* 3컬럼 그리드 */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">

          {/* KOSPI */}
          <div>
            <IndexBadge
              label="KOSPI"
              level={data.kospi.level}
              change_pct={data.kospi.change_pct}
              labelColor="text-indigo-300"
            />
            <div className="border-t border-gray-700 pt-2 min-h-[6rem]">
              <StockSlide stocks={kospiSlice} livePrices={livePrices} visible={visible} />
            </div>
          </div>

          {/* KOSDAQ */}
          <div>
            <IndexBadge
              label="KOSDAQ"
              level={data.kosdaq.level}
              change_pct={data.kosdaq.change_pct}
              labelColor="text-green-300"
            />
            <div className="border-t border-gray-700 pt-2 min-h-[6rem]">
              <StockSlide stocks={kosdaqSlice} livePrices={livePrices} visible={visible} />
            </div>
          </div>

          {/* NASDAQ */}
          <div>
            <IndexBadge
              label="NASDAQ"
              level={data.nasdaq.level}
              change_pct={data.nasdaq.change_pct}
              labelColor="text-purple-300"
              isUsd
            />
            <div className="border-t border-gray-700 pt-2 min-h-[6rem]">
              <StockSlide
                stocks={nasSlice}
                livePrices={{}}
                visible={visible}
                isUsd
              />
            </div>
          </div>

        </div>

        {/* 페이지 도트 */}
        <div className="flex justify-center gap-1 mt-3">
          {[0, 1].map((i) => (
            <div
              key={i}
              className={`w-1.5 h-1.5 rounded-full transition-colors duration-300 ${
                p === i ? "bg-gray-400" : "bg-gray-600"
              }`}
            />
          ))}
        </div>

      </div>
    </div>
  );
}

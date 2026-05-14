"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { api, Strategy, StrategyStats, Subscription, User, CandidateFilter, CandidateMarket, getToken } from "@/lib/api";
import StatCard from "@/components/StatCard";
import MarketOverviewPanel from "@/components/MarketOverview";

interface StrategyWithStats {
  strategy: Strategy;
  stats: StrategyStats | null;
}

const FILTER_LABELS: Record<CandidateFilter, string> = {
  volume:   "거래량 위주",
  largecap: "대형주 위주",
  mixed:    "혼합",
};
const FILTER_COLORS: Record<CandidateFilter, string> = {
  volume:   "bg-orange-900/60 text-orange-300",
  largecap: "bg-blue-900/60 text-blue-300",
  mixed:    "bg-gray-700 text-gray-300",
};
const MARKET_LABELS: Record<CandidateMarket, string> = {
  KOSPI:  "KOSPI",
  KOSDAQ: "KOSDAQ",
  NAS:    "NASDAQ",
  ALL:    "전 시장",
};
const MARKET_COLORS: Record<CandidateMarket, string> = {
  KOSPI:  "bg-indigo-900/60 text-indigo-300",
  KOSDAQ: "bg-green-900/60 text-green-300",
  NAS:    "bg-purple-900/60 text-purple-300",
  ALL:    "bg-gray-700 text-gray-300",
};

function getStrategyHint(hold_days: number, target_pct: string): {
  filter: CandidateFilter;
  market: CandidateMarket;
  reason: string;
} | null {
  const days = Number(hold_days);
  const tgt  = parseFloat(target_pct) || 0;
  if (!days || !tgt) return null;

  if (days <= 7) {
    if (tgt >= 15)
      return { filter: "volume", market: "KOSDAQ",
        reason: "단기 고수익은 변동성이 큰 KOSDAQ 거래량 상위 종목이 유리합니다." };
    if (tgt >= 8)
      return { filter: "volume", market: "ALL",
        reason: "단기 중수익은 거래량 위주 필터로 모멘텀 있는 종목을 넓게 탐색하는 게 효과적입니다." };
    return { filter: "mixed", market: "KOSPI",
      reason: "단기 저목표는 유동성 좋은 KOSPI 혼합 풀로도 충분히 달성 가능합니다." };
  }

  if (days <= 20) {
    if (tgt >= 15)
      return { filter: "mixed", market: "ALL",
        reason: "중기 고수익은 NASDAQ 성장주까지 포함한 전 시장 혼합 풀이 가장 유리합니다." };
    return { filter: "mixed", market: "ALL",
      reason: "중기 전략의 기본 조합입니다. 대형주와 중소형 모멘텀 종목을 균형 있게 탐색합니다." };
  }

  // 장기 (21일+)
  if (tgt >= 15)
    return { filter: "largecap", market: "ALL",
      reason: "장기 고수익은 펀더멘털 강한 대형주가 중심이 되어야 리스크 관리가 됩니다. NASDAQ 포함으로 성장주도 커버합니다." };
  return { filter: "largecap", market: "KOSPI",
    reason: "장기 안정 전략은 시총 상위 국내 대형주가 리스크 관리와 배당까지 가장 유리합니다." };
}

const DEFAULT_FORM = {
  name: "", description: "",
  hold_days: 20, target_pct: "6", stop_loss_pct: "3",
  min_probability: "60", pick_count: 3, run_interval_days: 3,
  candidate_filter: "mixed" as CandidateFilter,
  candidate_market: "ALL" as CandidateMarket,
  use_trailing_stop: false,
};

export default function DashboardPage() {
  const router = useRouter();
  const [me, setMe]             = useState<User | null>(null);
  const [items, setItems]       = useState<StrategyWithStats[]>([]);
  const [subMap, setSubMap]     = useState<Map<string, Subscription>>(new Map());
  const [scheduler, setScheduler] = useState<{ running: boolean; jobs: { id: string; next_run: string | null }[] } | null>(null);
  const [loading, setLoading]   = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm]         = useState(DEFAULT_FORM);
  const [creating, setCreating] = useState(false);
  const [msg, setMsg]           = useState("");

  useEffect(() => {
    if (!getToken()) { router.push("/login"); return; }
    load();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function load() {
    try {
      const user = await api.auth.me();
      setMe(user);
      const isAdmin = user.role === "ADMIN" || user.role === "SUPER_ADMIN";
      const [strategies, sched, subs] = await Promise.all([
        api.strategies.list(),
        isAdmin ? api.admin.schedulerStatus().catch(() => null) : Promise.resolve(null),
        api.strategies.mySubscriptions().catch(() => [] as Subscription[]),
      ]);
      setSubMap(new Map(subs.map((s) => [s.strategy_id, s])));
      const withStats = await Promise.all(
        strategies.map(async (s) => ({
          strategy: s,
          stats: await api.recommendations.stats(s.strategy_id).catch(() => null),
        }))
      );
      setItems(withStats);
      setScheduler(sched);
    } catch {
      router.push("/login");
    } finally {
      setLoading(false);
    }
  }

  async function createStrategy() {
    if (!form.name.trim()) { setMsg("전략명을 입력하세요"); return; }

    const tgt   = parseFloat(form.target_pct) || 0;
    const days  = Number(form.hold_days) || 1;
    const stop  = parseFloat(form.stop_loss_pct) || 0;
    const prob  = parseFloat(form.min_probability) || 0;
    const picks = Number(form.pick_count);

    if (picks > 4) { setMsg("픽 종목 수는 최대 4개입니다."); return; }
    if (prob < 55) { setMsg("최소 확률은 55% 이상이어야 합니다."); return; }
    if (stop > 0 && tgt / stop < 1.5) { setMsg(`R/R 비율 ${(tgt/stop).toFixed(2)}가 너무 낮습니다 (최소 1.5).`); return; }

    const dailyExpected = tgt / days;
    if (dailyExpected > 0.7) {
      const ok = window.confirm(
        `일평균 기대수익이 ${dailyExpected.toFixed(2)}%입니다.\n` +
        `AI가 무리한 근거를 만들어내거나 추천 품질이 크게 저하될 수 있습니다.\n\n` +
        `그래도 이 전략을 생성하시겠습니까?`
      );
      if (!ok) return;
    }
    setCreating(true); setMsg("");
    try {
      await api.strategies.create({
        name:              form.name.trim(),
        description:       form.description.trim() || null,
        hold_days:         Number(form.hold_days),
        target_pct:        form.target_pct,
        stop_loss_pct:     form.stop_loss_pct,
        min_probability:   form.min_probability,
        pick_count:        Number(form.pick_count),
        run_interval_days: Number(form.run_interval_days),
        candidate_filter:   form.candidate_filter,
        candidate_market:   form.candidate_market,
        use_trailing_stop:  form.use_trailing_stop,
      });
      setForm(DEFAULT_FORM);
      setShowCreate(false);
      await load();
    } catch (e: unknown) {
      setMsg(e instanceof Error ? e.message : "생성 실패");
    } finally {
      setCreating(false);
    }
  }

  if (loading)
    return (
      <div className="min-h-screen bg-gray-900 flex items-center justify-center text-gray-400">
        로딩 중...
      </div>
    );

  const nextStrategyRun = scheduler?.jobs.find((j) => j.id === "run_strategies")?.next_run;
  const isAdmin = me?.role === "ADMIN" || me?.role === "SUPER_ADMIN";

  return (
    <div className="min-h-screen bg-gray-900 text-white p-6">
      <div className="max-w-5xl mx-auto">

        {/* 헤더 */}
        <div className="flex items-center justify-between mb-8">
          <h1 className="text-2xl font-bold">Trading Dashboard</h1>
          <div className="flex items-center gap-3 text-sm">
            <span className={`w-2 h-2 rounded-full ${scheduler?.running ? "bg-green-400" : "bg-red-400"}`} />
            <span className="text-gray-400">
              스케줄러 {scheduler?.running ? "실행 중" : "정지"}
              {nextStrategyRun &&
                ` · 다음 실행: ${new Date(nextStrategyRun).toLocaleString("ko-KR")}`}
            </span>
            {isAdmin && (
              <button
                onClick={() => { setShowCreate((v) => !v); setMsg(""); }}
                className="bg-blue-600 hover:bg-blue-700 text-white rounded-lg px-3 py-1.5 text-xs font-medium"
              >
                {showCreate ? "닫기" : "+ 전략 만들기"}
              </button>
            )}
          </div>
        </div>

        {/* 시장 현황 — 전략 생성 폼 열리면 숨김 */}
        {!showCreate && <MarketOverviewPanel />}

        {/* 전략 생성 폼 */}
        {showCreate && (
          <div className="bg-gray-800 rounded-2xl p-6 mb-6">
            <h2 className="font-semibold text-gray-200 mb-4">새 전략</h2>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div className="sm:col-span-2">
                <label className="text-xs text-gray-400 mb-1 block">전략명 *</label>
                <input value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                  className="w-full bg-gray-700 rounded-lg px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-500"
                  placeholder="단기 거래량 전략" />
              </div>
              <div className="sm:col-span-2">
                <label className="text-xs text-gray-400 mb-1 block">설명</label>
                <input value={form.description} onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
                  className="w-full bg-gray-700 rounded-lg px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-500"
                  placeholder="선택 사항" />
              </div>

              {/* 후보 풀 추천 힌트 */}
              {(() => {
                const hint = getStrategyHint(form.hold_days, form.target_pct);
                if (!hint) return null;
                const FILTER_KO: Record<CandidateFilter, string> = { volume: "거래량 위주", largecap: "대형주 위주", mixed: "혼합" };
                const MARKET_KO: Record<CandidateMarket, string> = { ALL: "전 시장", KOSPI: "KOSPI", KOSDAQ: "KOSDAQ", NAS: "NASDAQ" };
                return (
                  <div className="sm:col-span-2 bg-blue-950/50 border border-blue-800/50 rounded-lg px-4 py-3 flex items-start gap-2">
                    <span className="text-blue-400 mt-0.5 shrink-0">💡</span>
                    <div className="text-xs text-blue-300 leading-relaxed">
                      <span className="font-medium">추천 조합: </span>
                      <button
                        type="button"
                        onClick={() => setForm((f) => ({ ...f, candidate_filter: hint.filter, candidate_market: hint.market }))}
                        className="underline underline-offset-2 hover:text-blue-100 transition-colors"
                      >
                        {FILTER_KO[hint.filter]} / {MARKET_KO[hint.market]}
                      </button>
                      <span className="text-blue-400 ml-1">— {hint.reason}</span>
                    </div>
                  </div>
                );
              })()}

              {/* 후보 풀 설정 */}
              <div>
                <label className="text-xs text-gray-400 mb-1 block">후보 필터</label>
                <select value={form.candidate_filter}
                  onChange={(e) => setForm((f) => ({ ...f, candidate_filter: e.target.value as CandidateFilter }))}
                  className="w-full bg-gray-700 rounded-lg px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-500">
                  <option value="volume">거래량 위주 — 단기 고수익 가능성</option>
                  <option value="largecap">대형주 위주 — 안정적 유동성</option>
                  <option value="mixed">혼합 — 거래량 + 대형주 + 균등 샘플</option>
                </select>
              </div>
              <div>
                <label className="text-xs text-gray-400 mb-1 block">후보 시장</label>
                <select value={form.candidate_market}
                  onChange={(e) => setForm((f) => ({ ...f, candidate_market: e.target.value as CandidateMarket }))}
                  className="w-full bg-gray-700 rounded-lg px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-500">
                  <option value="ALL">전 시장 (KOSPI + KOSDAQ + NASDAQ)</option>
                  <option value="KOSPI">KOSPI만</option>
                  <option value="KOSDAQ">KOSDAQ만</option>
                  <option value="NAS">NASDAQ만</option>
                </select>
              </div>

              {/* 트레일링 스탑 */}
              <div className="flex items-center justify-between bg-gray-700/50 rounded-lg px-3 py-2.5">
                <div>
                  <p className="text-sm text-gray-200">트레일링 스탑</p>
                  <p className="text-xs text-gray-400 mt-0.5">목표가 도달 후 즉시 익절 대신 고점 추적 후 손절</p>
                </div>
                <button
                  type="button"
                  onClick={() => setForm((f) => ({ ...f, use_trailing_stop: !f.use_trailing_stop }))}
                  className={`relative w-11 h-6 rounded-full transition-colors ${form.use_trailing_stop ? "bg-blue-500" : "bg-gray-600"}`}
                >
                  <span className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full shadow transition-transform ${form.use_trailing_stop ? "translate-x-5" : ""}`} />
                </button>
              </div>

              {/* 수치 파라미터 */}
              {([
                ["보유기간 (일)", "hold_days", 1, 365, null],
                ["목표수익률 (%)", "target_pct", 0.1, 100, null],
                ["손절라인 (%)", "stop_loss_pct", 0.1, 50, null],
                ["최소확률 (%, 최소 55)", "min_probability", 55, 100, null],
                ["픽 종목 수 (최대 4)", "pick_count", 1, 4, null],
                ["실행 주기 (일)", "run_interval_days", 1, 30, null],
              ] as [string, keyof typeof form, number, number, null][]).map(([label, key, min, max]) => (
                <div key={key}>
                  <label className="text-xs text-gray-400 mb-1 block">{label}</label>
                  <input type="number" min={min} max={max}
                    value={form[key] as string | number}
                    onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))}
                    onBlur={(e) => {
                      const val = parseFloat(e.target.value);
                      if (!isNaN(val)) {
                        const clamped = Math.min(max, Math.max(min, val));
                        if (clamped !== val) setForm((f) => ({ ...f, [key]: String(clamped) }));
                      }
                    }}
                    className="w-full bg-gray-700 rounded-lg px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-500" />
                </div>
              ))}
            </div>

            {/* 경고 메시지 */}
            {(() => {
              const days = Number(form.hold_days);
              const tgt  = parseFloat(form.target_pct) || 0;
              const stop = parseFloat(form.stop_loss_pct) || 0;
              const warnings: { level: "red" | "yellow"; msg: string }[] = [];

              if (days && tgt) {
                const daily = tgt / days;
                if (daily > 0.7)
                  warnings.push({ level: "red", msg: `일평균 기대수익 ${daily.toFixed(2)}% — 거의 불가능한 수준입니다. AI가 무리한 근거를 만들어낼 가능성이 높습니다.` });
                else if (daily > 0.5)
                  warnings.push({ level: "yellow", msg: `일평균 기대수익 ${daily.toFixed(2)}% — 달성이 어려울 수 있습니다.` });
              }

              if (tgt && stop) {
                const rr = tgt / stop;
                if (rr < 1.5)
                  warnings.push({ level: "red", msg: `R/R 비율 ${rr.toFixed(2)} — 최소 1.5 이상이어야 기대값이 양수입니다. 손절라인을 낮추거나 목표수익률을 높이세요.` });
              }

              if (!warnings.length) return null;
              return (
                <div className="mt-3 flex flex-col gap-2">
                  {warnings.map((w, i) => (
                    <div key={i} className={`rounded-lg px-4 py-3 flex items-start gap-2 text-xs ${
                      w.level === "red"
                        ? "bg-red-950/50 border border-red-800/50 text-red-300"
                        : "bg-yellow-950/50 border border-yellow-800/50 text-yellow-300"
                    }`}>
                      <span className="shrink-0 mt-0.5">{w.level === "red" ? "🚨" : "⚠️"}</span>
                      <span>{w.msg}</span>
                    </div>
                  ))}
                </div>
              );
            })()}

            {msg && <p className="text-sm text-red-400 mt-3">{msg}</p>}
            <button onClick={createStrategy} disabled={creating}
              className="mt-4 bg-blue-600 hover:bg-blue-700 text-white rounded-lg px-5 py-2 text-sm font-medium disabled:opacity-50">
              {creating ? "생성 중..." : "전략 생성"}
            </button>
          </div>
        )}

        {/* 전략 카드 목록 */}
        {items.length === 0 ? (
          <p className="text-gray-500">등록된 전략이 없습니다. {isAdmin && "위에서 전략을 만들어보세요."}</p>
        ) : (
          <div className="flex flex-col gap-6">
            {items.map(({ strategy, stats }) => {
              const sub = subMap.get(strategy.strategy_id);
              return (
              <div key={strategy.strategy_id} className="bg-gray-800 rounded-2xl p-6">
                <div className="flex items-start justify-between mb-3">
                  <div>
                    <div className="flex items-center flex-wrap gap-2">
                      <h2 className="text-lg font-semibold">{strategy.name}</h2>
                      <span className={`text-xs px-2 py-0.5 rounded-full ${strategy.is_active ? "bg-green-900 text-green-400" : "bg-gray-700 text-gray-400"}`}>
                        {strategy.is_active ? "활성" : "비활성"}
                      </span>
                      {/* 구독 배지 */}
                      {sub && (
                        <span className="text-xs px-2 py-0.5 rounded-full bg-yellow-900/60 text-yellow-300 flex items-center gap-1">
                          ★ 구독 중
                          <span className={`ml-1 ${sub.is_auto_trade ? "text-green-400" : "text-gray-400"}`}>
                            · 자동{sub.is_auto_trade ? "ON" : "OFF"}
                          </span>
                          <span className="text-gray-400 ml-1">
                            · {Number(sub.invest_amount_per_pick).toLocaleString()}원
                          </span>
                        </span>
                      )}
                      {/* 후보 필터 배지 */}
                      <span className={`text-xs px-2 py-0.5 rounded-full ${FILTER_COLORS[strategy.candidate_filter] ?? FILTER_COLORS.mixed}`}>
                        {FILTER_LABELS[strategy.candidate_filter] ?? strategy.candidate_filter}
                      </span>
                      <span className={`text-xs px-2 py-0.5 rounded-full ${MARKET_COLORS[strategy.candidate_market] ?? MARKET_COLORS.ALL}`}>
                        {MARKET_LABELS[strategy.candidate_market] ?? strategy.candidate_market}
                      </span>
                    </div>
                    <p className="text-sm text-gray-400 mt-1">{strategy.description ?? ""}</p>
                  </div>
                  <div className="flex items-center gap-3 ml-4 shrink-0">
                    <Link
                      href={`/strategies/${strategy.strategy_id}`}
                      className="text-sm text-blue-400 hover:text-blue-300 whitespace-nowrap"
                    >
                      추천 결과 보기 →
                    </Link>
                    {(isAdmin || String(strategy.created_by) === String(me?.user_id)) && (
                      <button
                        onClick={async () => {
                          if (!confirm(`"${strategy.name}" 전략을 비활성화하시겠습니까?\n스케줄러 분석이 중단되며 기존 데이터는 유지됩니다.`)) return;
                          try {
                            await api.strategies.deactivate(strategy.strategy_id);
                            await load();
                          } catch { alert("비활성화 실패"); }
                        }}
                        className="text-xs text-gray-500 hover:text-red-400 border border-gray-700 hover:border-red-800 px-2 py-1 rounded transition-colors"
                      >비활성화</button>
                    )}
                  </div>
                </div>

                {/* 파라미터 */}
                <div className="flex flex-wrap gap-3 text-xs text-gray-500 mb-4">
                  <span>보유 {strategy.hold_days}일</span>
                  <span>목표 +{strategy.target_pct}%</span>
                  <span>손절 -{strategy.stop_loss_pct}%</span>
                  <span>최소확률 {strategy.min_probability}%</span>
                  <span>픽 {strategy.pick_count}개</span>
                  <span>주기 {strategy.run_interval_days}일</span>
                </div>

                {/* 성과 통계 */}
                {stats ? (() => {
                  const aiVsRandom = stats.avg_pnl_pct != null && stats.random_avg_pnl != null
                    ? stats.avg_pnl_pct - stats.random_avg_pnl : null;
                  return (
                  <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
                    <StatCard label="총 실행" value={stats.total_runs} sub="회" />
                    <StatCard
                      label="승률"
                      value={stats.win_rate != null ? `${(stats.win_rate * 100).toFixed(1)}%` : "-"}
                      sub={stats.total_verified > 0 ? `${stats.success_count}승 ${stats.fail_count}패` : "검증 대기 중"}
                      color={stats.win_rate != null ? (stats.win_rate >= 0.5 ? "green" : "red") : "gray"}
                    />
                    <StatCard
                      label="AI 평균수익"
                      value={stats.avg_pnl_pct != null ? `${stats.avg_pnl_pct >= 0 ? "+" : ""}${stats.avg_pnl_pct.toFixed(2)}%` : "-"}
                      color={stats.avg_pnl_pct != null ? (stats.avg_pnl_pct >= 0 ? "green" : "red") : "gray"}
                    />
                    <StatCard
                      label="랜덤 평균수익"
                      value={stats.random_avg_pnl != null ? `${stats.random_avg_pnl >= 0 ? "+" : ""}${stats.random_avg_pnl.toFixed(2)}%` : "-"}
                      sub="시장 효과"
                      color={stats.random_avg_pnl != null ? (stats.random_avg_pnl >= 0 ? "green" : "red") : "gray"}
                    />
                    <StatCard
                      label="AI 우위"
                      value={aiVsRandom != null ? `${aiVsRandom >= 0 ? "+" : ""}${aiVsRandom.toFixed(2)}%p` : "-"}
                      sub="AI - 랜덤"
                      color={aiVsRandom != null ? (aiVsRandom >= 0 ? "green" : "red") : "gray"}
                    />
                    <StatCard
                      label="성공/실패 평균"
                      value={stats.success_avg_pnl != null ? `${stats.success_avg_pnl >= 0 ? "+" : ""}${stats.success_avg_pnl.toFixed(1)}%` : "-"}
                      sub={stats.fail_avg_pnl != null ? `실패 ${stats.fail_avg_pnl >= 0 ? "+" : ""}${stats.fail_avg_pnl.toFixed(1)}%` : "실패 데이터 없음"}
                      color={stats.success_avg_pnl != null ? "green" : "gray"}
                    />
                  </div>
                  );
                })() : (
                  <p className="text-sm text-gray-500">통계 없음</p>
                )}
              </div>
              );
            })}
          </div>
        )}

        {/* 스케줄러 잡 목록 */}
        {scheduler?.running && (
          <div className="mt-8 bg-gray-800 rounded-2xl p-6">
            <h3 className="font-semibold mb-3 text-gray-300">스케줄러 잡</h3>
            <div className="flex flex-col gap-2">
              {scheduler.jobs.map((job) => (
                <div key={job.id} className="flex justify-between text-sm">
                  <span className="text-gray-400 font-mono">{job.id}</span>
                  <span className="text-gray-300">
                    {job.next_run ? new Date(job.next_run).toLocaleString("ko-KR") : "-"}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

      </div>
    </div>
  );
}

const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1";

// ------------------------------------------------------------------ //
// Token
// ------------------------------------------------------------------ //
let _token: string | null = null;

export function getToken(): string | null {
  if (_token) return _token;
  if (typeof window !== "undefined") _token = localStorage.getItem("token");
  return _token;
}
export function setToken(t: string) {
  _token = t;
  if (typeof window !== "undefined") localStorage.setItem("token", t);
}
export function clearToken() {
  _token = null;
  if (typeof window !== "undefined") localStorage.removeItem("token");
}

// ------------------------------------------------------------------ //
// Base fetch
// ------------------------------------------------------------------ //
async function authFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getToken();
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init?.headers,
    },
  });
  if (res.status === 401) {
    clearToken();
    if (typeof window !== "undefined") window.location.href = "/login";
    throw new Error("Unauthorized");
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail ?? `API ${res.status}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

// ------------------------------------------------------------------ //
// Types
// ------------------------------------------------------------------ //
export type UserRole = "SUPER_ADMIN" | "ADMIN" | "TRADER" | "VIEWER";

export interface User {
  user_id: string;
  username: string;
  email: string;
  role: UserRole;
  is_active: boolean;
  telegram_chat_id: string | null;
  created_at: string;
}

export type CandidateFilter = "volume" | "largecap" | "mixed";
export type CandidateMarket = "KOSPI" | "KOSDAQ" | "NAS" | "ALL";

export interface Strategy {
  strategy_id: string;
  name: string;
  description: string | null;
  hold_days: number;
  target_pct: string;
  stop_loss_pct: string;
  min_probability: string;
  pick_count: number;
  run_interval_days: number;
  candidate_filter: CandidateFilter;
  candidate_market: CandidateMarket;
  use_trailing_stop: boolean;
  is_active: boolean;
  created_by: string;
}

export interface Verification {
  verify_id: string;
  verified_at: string;
  price_at_verify: string | null;
  max_high: string | null;
  max_low: string | null;
  result: "SUCCESS" | "FAIL" | null;
  pnl_pct: string | null;
}

export interface Recommendation {
  rec_id: string;
  stock_code: string;
  stock_name: string;
  target_price: string | null;
  stop_loss_price: string | null;
  ai_probability: string | null;
  ai_reason: string | null;
  risk_factors: string | null;
  historical_basis: string | null;
  rank: number | null;
  verification: Verification | null;
}

export interface StockMasterItem {
  stock_code: string;
  stock_name: string;
  market: string;
  country: string;
  sector: string | null;
}

export interface RecommendationRun {
  run_id: string;
  strategy_id: string;
  run_date: string;
  ai_model_used: string | null;
  stage1_model: string | null;
  stage2_model: string | null;
  stage3_model: string | null;
  stage4_model: string | null;
  prompt_version: string | null;
  recommendations: Recommendation[];
}

export interface StrategyStats {
  strategy_id: string;
  total_runs: number;
  total_picks: number;
  total_verified: number;
  success_count: number;
  fail_count: number;
  win_rate: number | null;
  avg_pnl_pct: number | null;
  success_avg_pnl: number | null;
  fail_avg_pnl: number | null;
  random_avg_pnl: number | null;
  expected_value: number | null;
}

export type PositionStatus =
  | "HOLDING" | "TARGET_HIT" | "STOP_LOSS" | "EXPIRED" | "MANUAL_EXIT";

export interface Position {
  position_id: string;
  user_id: string;
  strategy_id: string | null;
  rec_id: string | null;
  account_id: string;
  stock_code: string;
  entry_price: string;
  entry_date: string;
  quantity: number;
  status: PositionStatus;
  exit_price: string | null;
  exit_date: string | null;
  pnl_pct: string | null;
  peak_price: string | null;
  target_price: string | null;
  trailing_stop_price: string | null;
}

export interface BrokerAccount {
  account_id: string;
  broker: string;
  account_no: string;
  account_type: string;
  hts_id: string | null;
  is_active: boolean;
}

export interface Subscription {
  id: number;
  user_id: string;
  strategy_id: string;
  account_id: string;
  invest_amount_per_pick: string;
  is_auto_trade: boolean;
  is_active: boolean;
  subscribed_at: string;
}

export interface NewsWatchConfig {
  interval_min: number;
  paused: boolean;
  pause_reason: string;
  last_check_at: string;
  today_usage: number;
  daily_estimate: number;
  rpd_limit: number;
}

export interface CloseAllResult {
  closed: number;
  results: { stock_code: string; status: string; pnl_pct?: number; error?: string }[];
}

export interface SchedulerJob {
  id: string;
  trigger: string;
  next_run: string | null;
}

export interface BacktestPickResult {
  stock_code: string;
  stock_name: string;
  result: "SUCCESS" | "FAIL";
  pnl_pct: number | null;
}

export interface BacktestDateResult {
  date: string;
  run_id?: string;
  picks: BacktestPickResult[];
  win_rate: number | null;
  avg_pnl: number | null;
  success_avg_pnl: number | null;
  fail_avg_pnl: number | null;
  random_avg_pnl: number | null;
  error?: string;
}

export interface BacktestSummary {
  win_rate: number | null;
  avg_pnl: number | null;
  success_avg_pnl: number | null;
  fail_avg_pnl: number | null;
  random_avg_pnl: number | null;
  total_picks: number;
  success_count: number;
  fail_count: number;
}

export interface BacktestResult {
  status: string;
  strategy_name: string;
  base_date: string;
  dates_attempted: number;
  dates_succeeded: number;
  skipped: string[];
  summary: BacktestSummary;
  results: BacktestDateResult[];
}

export interface BacktestRunSummary {
  run_id: string;
  run_date: string;
  picks: number;
  verified: number;
  success: number;
  avg_pnl: number | null;
  success_avg_pnl: number | null;
  fail_avg_pnl: number | null;
  random_avg_pnl: number | null;
}

export interface BacktestMonthlyRow {
  month: string;
  picks: number;
  win_rate: number | null;
  ai_avg_pnl: number | null;
  rand_avg_pnl: number | null;
  advantage: number | null;
}

export interface BacktestOverallSummary {
  total_runs: number;
  total_picks: number;
  win_rate: number | null;
  ai_avg_pnl: number | null;
  ai_success_avg: number | null;
  ai_fail_avg: number | null;
  rand_avg_pnl: number | null;
  advantage: number | null;
  monthly: BacktestMonthlyRow[];
}

export interface IndexInfo {
  level: number;
  change_pct: number;
}

export interface StockSnap {
  code: string;
  name: string;
  price: number;   // KRW: int, USD: float
  change_pct: number;
}

export interface MarketOverview {
  kospi: IndexInfo;
  kosdaq: IndexInfo;
  nasdaq: IndexInfo;
  kospi_stocks: StockSnap[];
  kosdaq_stocks: StockSnap[];
  nasdaq_stocks: StockSnap[];
  cached_at: number;
}

export interface TradeKPI {
  total_trades: number;
  win_count: number;
  loss_count: number;
  win_rate: number | null;
  avg_win_pct: number | null;
  avg_loss_pct: number | null;
  profit_factor: number | null;
  total_pnl_amount: number;
  avg_hold_days: number | null;
  sharpe: number | null;
  max_drawdown_pct: number | null;
}

export interface StrategyKPI extends TradeKPI {
  strategy_id: string;
  strategy_name: string;
}

export interface MonthStat {
  month: string;
  total_trades: number;
  win_count: number;
  total_pnl_amount: number;
  avg_pnl_pct: number;
}

export interface StockStat {
  stock_code: string;
  stock_name: string;
  total_trades: number;
  win_count: number;
  total_pnl_amount: number;
  avg_pnl_pct: number;
}

export interface TradeRow {
  position_id: string;
  stock_code: string;
  stock_name: string;
  strategy_name: string;
  entry_price: number;
  exit_price: number;
  quantity: number;
  pnl_pct: number;
  pnl_amount: number;
  hold_days: number | null;
  exit_date: string | null;
  status: string;
}

export interface ProfitStats {
  overall: TradeKPI;
  by_strategy: StrategyKPI[];
  by_month: MonthStat[];
  by_stock: StockStat[];
  trades: TradeRow[];
}

export interface NewsEvent {
  event_id: string;
  detected_at: string;
  severity: "NORMAL" | "WARNING" | "CRITICAL";
  event_description: string;
  keywords: string[];
  ai_confidence: number | null;
  kospi_at_detection: number | null;
  kosdaq_at_detection: number | null;
  kospi_change_1d: number | null;
  kospi_change_3d: number | null;
  kosdaq_change_1d: number | null;
  kosdaq_change_3d: number | null;
  verified_1d: boolean;
  verified_3d: boolean;
}

// ------------------------------------------------------------------ //
// API
// ------------------------------------------------------------------ //
export const api = {
  auth: {
    login: async (username: string, password: string) => {
      const res = await fetch(`${BASE}/users/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      if (!res.ok) throw new Error("로그인 실패");
      const data = await res.json();
      setToken(data.access_token);
    },
    register: async (username: string, email: string, password: string) => {
      const res = await fetch(`${BASE}/users/register`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, email, password, role: "TRADER" }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail ?? "회원가입 실패");
      }
      return res.json();
    },
    me: () => authFetch<User>("/users/me"),
  },

  strategies: {
    list: () => authFetch<Strategy[]>("/strategies"),
    create: (body: {
      name: string; description?: string | null;
      hold_days: number; target_pct: string; stop_loss_pct: string;
      min_probability: string; pick_count: number; run_interval_days: number;
      candidate_filter: CandidateFilter; candidate_market: CandidateMarket;
      use_trailing_stop?: boolean;
    }) => authFetch<Strategy>("/strategies", { method: "POST", body: JSON.stringify(body) }),
    update: (id: string, body: Partial<{
      name: string; description: string | null;
      hold_days: number; target_pct: string; stop_loss_pct: string;
      min_probability: string; pick_count: number; run_interval_days: number;
      candidate_filter: CandidateFilter; candidate_market: CandidateMarket;
      use_trailing_stop: boolean;
      is_active: boolean;
    }>) => authFetch<Strategy>(`/strategies/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
    subscribe: (body: {
      strategy_id: string; account_id: string;
      invest_amount_per_pick: number; is_auto_trade: boolean;
    }) => authFetch<Subscription>("/strategies/subscribe", { method: "POST", body: JSON.stringify(body) }),
    mySubscriptions: () => authFetch<Subscription[]>("/strategies/my/subscriptions"),
    updateSubscription: (subId: number, body: { invest_amount_per_pick?: number; is_auto_trade?: boolean; account_id?: string }) =>
      authFetch<Subscription>(`/strategies/subscriptions/${subId}`, { method: "PATCH", body: JSON.stringify(body) }),
    deactivate: (id: string) => authFetch<void>(`/strategies/${id}`, { method: "DELETE" }),
    unsubscribe: (subId: number) => authFetch<void>(`/strategies/subscriptions/${subId}`, { method: "DELETE" }),
    toggleAutoTrade: (subId: number) => authFetch<Subscription>(`/strategies/subscriptions/${subId}/auto-trade`, { method: "PATCH" }),
  },

  recommendations: {
    runs: (strategyId: string) =>
      authFetch<RecommendationRun[]>(`/recommendations/runs?strategy_id=${strategyId}`),
    stats: (strategyId: string) =>
      authFetch<StrategyStats>(`/recommendations/stats/${strategyId}`),
  },

  positions: {
    stats: () => authFetch<ProfitStats>("/positions/stats"),
    list: (status?: PositionStatus) =>
      authFetch<Position[]>(`/positions${status ? `?status=${status}` : ""}`),
    close: (positionId: string) =>
      authFetch<Position>(`/positions/${positionId}/close`, { method: "POST" }),
    closeAll: () =>
      authFetch<CloseAllResult>("/positions/close-all", { method: "POST" }),
    manualBuy: (body: { stock_code: string; account_id: string; amount: number; strategy_id?: string }) =>
      authFetch<Position>("/positions/manual-buy", { method: "POST", body: JSON.stringify(body) }),
  },

  users: {
    list: () => authFetch<User[]>("/users"),
    update: (userId: string, body: Partial<Pick<User, "role" | "is_active">>) =>
      authFetch<User>(`/users/${userId}`, { method: "PATCH", body: JSON.stringify(body) }),
    updateTelegram: (chatId: string | null) =>
      authFetch<User>("/users/me/telegram", {
        method: "PATCH",
        body: JSON.stringify({ telegram_chat_id: chatId }),
      }),
    addBrokerAccount: (userId: string, body: {
      account_no: string; api_key: string; api_secret: string; account_type: string;
    }) =>
      authFetch<BrokerAccount>(`/users/${userId}/accounts`, {
        method: "POST",
        body: JSON.stringify({ broker: "KIS", ...body }),
      }),
    listBrokerAccounts: (userId: string) =>
      authFetch<BrokerAccount[]>(`/users/${userId}/accounts`),
    updateBrokerAccount: (userId: string, accountId: string, body: { hts_id?: string | null }) =>
      authFetch<BrokerAccount>(`/users/${userId}/accounts/${accountId}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      }),
  },

  market: {
    stockBasic: (code: string) =>
      authFetch<{ stock_code: string; stock_name: string; market: string; sector: string }>(
        `/market/stock-basic/${code}`
      ),
    price: (code: string) =>
      authFetch<{ stock_code: string; currency: string; current_price: number; open_price: number; change_pct: number }>(
        `/market/price/${code}`
      ),
    overview: () => authFetch<MarketOverview>("/market/overview"),
  },

  stockMaster: {
    search: (q: string, market?: string) =>
      authFetch<StockMasterItem[]>(
        `/stock-master/search?q=${encodeURIComponent(q)}${market ? `&market=${market}` : ""}&limit=15`
      ),
    stats: () => authFetch<Record<string, number>>("/stock-master/stats"),
    triggerUpdate: () =>
      authFetch("/stock-master/update", { method: "POST" }),
    triggerCandidateRefresh: () =>
      authFetch("/stock-master/refresh-candidates", { method: "POST" }),
  },

  admin: {
    schedulerStatus: () =>
      authFetch<{ running: boolean; jobs: SchedulerJob[] }>("/admin/scheduler/status"),
    runStrategy: (id: string) =>
      authFetch(`/admin/strategies/${id}/run`, { method: "POST" }),
    runBacktest: (id: string, baseDate: string) =>
      authFetch<BacktestResult>(`/admin/backtest/strategies/${id}`, {
        method: "POST",
        body: JSON.stringify({ base_date: baseDate }),
      }),
    getBacktestResults: (id: string) =>
      authFetch<BacktestRunSummary[]>(`/admin/backtest/strategies/${id}/results`),
    getBacktestSummary: (id: string) =>
      authFetch<BacktestOverallSummary>(`/admin/backtest/strategies/${id}/summary`),
    getNewsWatchConfig: () => authFetch<NewsWatchConfig>("/admin/news-watch/config"),
    updateNewsWatchInterval: (interval_min: number) =>
      authFetch<{ interval_min: number }>("/admin/news-watch/config", {
        method: "PATCH", body: JSON.stringify({ interval_min }),
      }),
    resumeAutoTrade: () => authFetch("/admin/news-watch/resume", { method: "POST" }),
  },

  newsEvents: {
    list: (severity?: string) =>
      authFetch<NewsEvent[]>(`/news-events${severity ? `?severity=${severity}` : ""}`),
  },
};

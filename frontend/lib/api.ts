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
  is_active: boolean;
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

export interface CandidateStock {
  stock_id: number;
  stock_code: string;
  stock_name: string;
  market: string | null;
  sector: string | null;
  is_active: boolean;
  notes: string | null;
}

export interface RecommendationRun {
  run_id: string;
  strategy_id: string;
  run_date: string;
  ai_model_used: string | null;
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
  expected_value: number | null;
}

export type PositionStatus =
  | "HOLDING" | "TARGET_HIT" | "STOP_LOSS" | "EXPIRED" | "MANUAL_EXIT";

export interface Position {
  position_id: string;
  user_id: string;
  strategy_id: string;
  rec_id: string;
  account_id: string;
  stock_code: string;
  entry_price: string;
  entry_date: string;
  quantity: number;
  status: PositionStatus;
  exit_price: string | null;
  exit_date: string | null;
  pnl_pct: string | null;
}

export interface BrokerAccount {
  account_id: string;
  broker: string;
  account_no: string;
  account_type: string;
  is_active: boolean;
}

export interface SchedulerJob {
  id: string;
  trigger: string;
  next_run: string | null;
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
  },

  recommendations: {
    runs: (strategyId: string) =>
      authFetch<RecommendationRun[]>(`/recommendations/runs?strategy_id=${strategyId}`),
    stats: (strategyId: string) =>
      authFetch<StrategyStats>(`/recommendations/stats/${strategyId}`),
  },

  positions: {
    list: (status?: PositionStatus) =>
      authFetch<Position[]>(`/positions${status ? `?status=${status}` : ""}`),
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
  },

  stocks: {
    list: (activeOnly = true) =>
      authFetch<CandidateStock[]>(`/candidate-stocks?active_only=${activeOnly}`),
    create: (body: Pick<CandidateStock, "stock_code" | "stock_name" | "market" | "sector" | "notes">) =>
      authFetch<CandidateStock>("/candidate-stocks", { method: "POST", body: JSON.stringify(body) }),
    update: (id: number, body: Partial<Pick<CandidateStock, "stock_name" | "market" | "sector" | "is_active" | "notes">>) =>
      authFetch<CandidateStock>(`/candidate-stocks/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
    remove: (id: number) =>
      authFetch<void>(`/candidate-stocks/${id}`, { method: "DELETE" }),
    bulkImport: (items: Pick<CandidateStock, "stock_code" | "stock_name" | "market" | "sector">[]) =>
      authFetch<{ created: number; skipped: number }>("/candidate-stocks/bulk-import", {
        method: "POST", body: JSON.stringify(items),
      }),
  },

  admin: {
    schedulerStatus: () =>
      authFetch<{ running: boolean; jobs: SchedulerJob[] }>("/admin/scheduler/status"),
    runStrategy: (id: string) =>
      authFetch(`/admin/strategies/${id}/run`, { method: "POST" }),
  },
};

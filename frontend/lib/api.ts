const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1";

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

export async function login(username: string, password: string): Promise<void> {
  const res = await fetch(`${BASE}/users/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) throw new Error("로그인 실패");
  const data = await res.json();
  setToken(data.access_token);
}

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
    if (typeof window !== "undefined") {
      localStorage.removeItem("token");
      window.location.href = "/login";
    }
    throw new Error("Unauthorized");
  }
  if (!res.ok) throw new Error(`API ${res.status}`);
  return res.json();
}

// ------------------------------------------------------------------ //
// Types
// ------------------------------------------------------------------ //

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

export interface Recommendation {
  rec_id: string;
  stock_code: string;
  stock_name: string;
  target_price: string | null;
  stop_loss_price: string | null;
  ai_probability: string | null;
  ai_reason: string | null;
  rank: number | null;
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

export interface SchedulerJob {
  id: string;
  trigger: string;
  next_run: string | null;
}

// ------------------------------------------------------------------ //
// API
// ------------------------------------------------------------------ //

export const api = {
  strategies: {
    list: () => authFetch<Strategy[]>("/strategies"),
  },
  recommendations: {
    runs: (strategyId: string) =>
      authFetch<RecommendationRun[]>(`/recommendations/runs?strategy_id=${strategyId}`),
    stats: (strategyId: string) =>
      authFetch<StrategyStats>(`/recommendations/stats/${strategyId}`),
  },
  admin: {
    schedulerStatus: () =>
      authFetch<{ running: boolean; jobs: SchedulerJob[] }>("/admin/scheduler/status"),
    runStrategy: (id: string) =>
      authFetch(`/admin/strategies/${id}/run`, { method: "POST" }),
  },
};

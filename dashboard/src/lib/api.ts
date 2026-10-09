import "server-only";

import type {
  CaseDetail,
  CaseSummary,
  Execution,
  OutboxMessage,
  Policy,
  SimulationOverview,
  SimulationRun,
  Stats,
} from "./types";

// Server-side only: the browser never talks to the agent service, so the admin key never leaves
// the server. AGENT_API_URL and ADMIN_API_KEY come from dashboard/.env.local.
const BASE = (process.env.AGENT_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit & { admin?: boolean }): Promise<T> {
  const headers = new Headers(init?.headers);
  if (init?.body) headers.set("content-type", "application/json");
  if (init?.admin) {
    const key = process.env.ADMIN_API_KEY;
    if (!key) throw new ApiError("Set ADMIN_API_KEY in dashboard/.env.local to take actions", 503);
    headers.set("x-admin-key", key);
  }
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, { ...init, headers, cache: "no-store" });
  } catch {
    throw new ApiError(`The agent service at ${BASE} is not reachable`, 0);
  }
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    } catch {
      /* not JSON */
    }
    throw new ApiError(detail, response.status);
  }
  return response.json() as Promise<T>;
}

const query = (params: Record<string, string | string[] | number | undefined>) => {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === "") continue;
    for (const item of Array.isArray(v) ? v : [v]) q.append(k, String(item));
  }
  const s = q.toString();
  return s ? `?${s}` : "";
};

export const api = {
  baseUrl: BASE,
  /** True when the agent service and its database answer. */
  healthy: async (): Promise<boolean> => {
    try {
      const r = await fetch(`${BASE}/health`, { cache: "no-store", signal: AbortSignal.timeout(3000) });
      return r.ok;
    } catch {
      return false;
    }
  },
  stats: (days: number) => request<Stats>(`/stats${query({ days })}`),
  cases: (params: { status?: string[]; case_type?: string[]; limit?: number }) =>
    request<CaseSummary[]>(`/cases${query({ ...params, limit: params.limit ?? 200 })}`),
  case: (id: string) => request<CaseDetail>(`/cases/${encodeURIComponent(id)}`),
  executions: (params: { status?: string[]; limit?: number }) =>
    request<Execution[]>(`/executions${query({ ...params, limit: params.limit ?? 100 })}`),
  outbox: (params: { status?: string[]; limit?: number }) =>
    request<OutboxMessage[]>(`/outbox${query({ ...params, limit: params.limit ?? 100 })}`),
  policies: () => request<Policy[]>("/policies"),
  simulation: () => request<SimulationOverview>("/simulation"),

  // Actions (admin key).
  approve: (id: string, by: string, note?: string) =>
    request<Execution>(`/executions/${id}/approve`, { method: "POST", admin: true, body: JSON.stringify({ by, note }) }),
  reject: (id: string, by: string, note?: string) =>
    request<Execution>(`/executions/${id}/reject`, { method: "POST", admin: true, body: JSON.stringify({ by, note }) }),
  complete: (id: string, by: string, succeeded: boolean, note: string) =>
    request<Execution>(`/executions/${id}/complete`, {
      method: "POST",
      admin: true,
      body: JSON.stringify({ by, succeeded, note }),
    }),
  rollback: (id: string, by: string, reason: string) =>
    request<Execution>(`/executions/${id}/rollback`, {
      method: "POST",
      admin: true,
      body: JSON.stringify({ by, reason }),
    }),
  detect: () =>
    request<{ found: number; opened: number; updated: number; reclassified: number; closed: number }>("/detect", {
      method: "POST",
      admin: true,
    }),
  dryRun: () => request<SimulationRun>("/simulation/dry-run", { method: "POST", admin: true }),
  simulateOutcomes: (episodes: number, seed: number) =>
    request<SimulationRun>("/simulation/outcomes", { method: "POST", admin: true, body: JSON.stringify({ episodes, seed }) }),
  calibrate: () => request<SimulationRun>("/simulation/calibrate", { method: "POST", admin: true }),
  investigate: (id: string) => request<CaseSummary>(`/cases/${id}/investigate`, { method: "POST", admin: true }),
  plan: (id: string) => request<CaseSummary>(`/cases/${id}/plan`, { method: "POST", admin: true }),
  act: (id: string) =>
    request<{ executions: Execution[]; waiting: boolean }>(`/cases/${id}/act`, { method: "POST", admin: true }),
};

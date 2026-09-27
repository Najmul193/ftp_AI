/** Typed client for the optional AI module (`/api/v1/ai`).
 *
 *  When the backend runs without the module the routes do not exist: `status`
 *  then resolves to null and the UI shows nothing AI-related. */

import { ApiError, request } from "../api";

export type Tier = "PUBLIC" | "AGGREGATE" | "RESTRICTED";

export interface AiStatus {
  module: boolean;
  enabled: boolean;
  provider: { brand: string; label: string; model: string } | null;
  can_view: boolean;
  can_chat: boolean;
  can_admin: boolean;
  can_audit: boolean;
}

export interface Preset {
  brand: string; label: string; kind: string; base_url: string;
  needs_key: boolean; free_tier: boolean; local: boolean;
  suggested_models: string[]; key_url: string; note: string;
}

export interface Provider {
  id: number; brand: string; kind: string; label: string; base_url: string;
  model: string; key_last4: string | null; has_key: boolean;
  max_data_tier: Tier; is_free_tier: boolean; trial_ack_at: string | null;
  status: "ok" | "failed" | "untested"; status_detail: string | null;
  last_tested_at: string | null; daily_token_budget: number; tokens_today?: number;
}

export interface Policy {
  actions: Record<string, string>;
  amount_mode: "crore_3sf" | "index";
}

export interface AiSettings {
  enabled: boolean;
  active_provider_id: number | null;
  policy: Policy;
  policy_options: Record<string, string[]>;
  hard_drop: string[];
  bank_tier: Tier;
  providers: Provider[];
}

export interface ProbeResult {
  ok: boolean; models: string[]; reply: string | null;
  error_code: string | null; error: string | null; latency_ms: number | null;
}

export interface TryResult {
  you_typed: string; sent: string; answer: string; provider_answer: string;
  tier: Tier; provider: string; model: string; request_id: number;
}

export interface EgressItem {
  id: number; created_at: string; username: string | null; purpose: string;
  provider: string | null; model: string | null; tier: Tier;
  status: "ok" | "blocked" | "error"; blocked_reason: string | null;
  payload: { system: string; messages: { role: string; segments: string[]; content: string }[] };
  response: string | null; grounded: boolean | null; unverified: string[] | null;
  tokens_in: number | null; tokens_out: number | null; latency_ms: number | null;
}

export interface NewProvider {
  brand: string; model: string; api_key?: string; base_url?: string; label?: string;
  max_data_tier: Tier; is_free_tier?: boolean; trial_ack: boolean; daily_token_budget: number;
}

const json = (body: unknown): RequestInit => ({ body: JSON.stringify(body) });

export const aiApi = {
  /** Null when the backend runs without the AI module. */
  status: async (): Promise<AiStatus | null> => {
    try { return await request<AiStatus>("/ai/status"); }
    catch (e) { if (e instanceof ApiError && e.status === 404) return null; throw e; }
  },
  presets: () => request<Preset[]>("/ai/presets"),
  settings: () => request<AiSettings>("/ai/settings"),
  setEnabled: (enabled: boolean) =>
    request<{ enabled: boolean }>("/ai/settings/enabled", { method: "PUT", ...json({ enabled }) }),
  setPolicy: (p: Policy) =>
    request<{ policy: Policy; bank_tier: Tier }>("/ai/settings/policy", { method: "PUT", ...json(p) }),
  probe: (b: { brand: string; base_url?: string; api_key?: string; model?: string }) =>
    request<ProbeResult>("/ai/providers/probe", { method: "POST", ...json(b) }),
  createProvider: (b: NewProvider) =>
    request<{ provider: Provider; test: ProbeResult }>("/ai/providers", { method: "POST", ...json(b) }),
  updateProvider: (id: number, b: Partial<NewProvider>) =>
    request<{ provider: Provider }>(`/ai/providers/${id}`, { method: "PATCH", ...json(b) }),
  deleteProvider: (id: number) => request<void>(`/ai/providers/${id}`, { method: "DELETE" }),
  testProvider: (id: number) =>
    request<{ provider: Provider; test: ProbeResult }>(`/ai/providers/${id}/test`, { method: "POST" }),
  activate: (id: number) =>
    request<{ active_provider_id: number }>(`/ai/providers/${id}/activate`, { method: "POST" }),
  try: (prompt: string) => request<TryResult>("/ai/try", { method: "POST", ...json({ prompt }) }),
  egressLog: (p: { limit?: number; offset?: number; status?: string } = {}) => {
    const q = new URLSearchParams();
    Object.entries(p).forEach(([k, v]) => v !== undefined && v !== "" && q.set(k, String(v)));
    return request<{ total: number; items: EgressItem[] }>(`/ai/egress-log?${q}`);
  },
};

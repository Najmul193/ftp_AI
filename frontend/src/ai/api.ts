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
  /** Unread serious and critical insights: the bell's count. */
  unread: number;
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

// --- market data -------------------------------------------------------------

export interface MarketSeries {
  code: string; name: string; short: string; category: string; unit: string; source: string;
  tenor_days: number | null; value: number | null; as_of: string | null;
  previous: number | null; previous_as_of: string | null; change: number | null;
  stale: boolean; spark: number[]; entered_by: number | null; source_ref: string | null;
}

export interface CurvePoint { code: string; label: string; tenor_days: number; value: number; as_of: string }

export interface BenchmarkRow {
  product_code: string; name: string; side: "ASSET" | "LIABILITY"; tenor_days: number;
  tenor_basis: string; benchmark: number | null; market: number | null; market_basis: string | null;
  gap_bp: number | null; balance: number | null; monthly_impact: number | null; behavioural: boolean;
}

export interface JobState {
  last_status: string | null; last_run: string | null; last_success: string | null;
  errors: string[] | null;
}

export interface MarketOverview {
  series: MarketSeries[];
  curve: CurvePoint[];
  benchmarks: { items: BenchmarkRow[]; balances_as_of: string | null; error?: string };
  jobs: Record<string, JobState>;
}

export interface NewsItem {
  id: number; source: string; title: string; url: string; published_at: string | null;
  summary: string | null; region: "BD" | "GLOBAL"; tags: string[]; impacts: string[];
  rate_signal: number; relevance: number;
}

export interface ParsedItem {
  code: string; name: string; unit: string; obs_date: string | null; value: number;
  evidence: string; current: number | null; current_as_of: string | null;
}

export interface ParseResult {
  kind: "call_money" | "ref_rates" | "auctions" | "unknown";
  page_date: string | null; warnings: string[]; items: ParsedItem[];
}

export interface Entry { code: string; obs_date: string; value: number | string; ref?: string }

export const marketApi = {
  overview: () => request<MarketOverview>("/ai/market/overview"),
  news: (p: { tag?: string; region?: string; q?: string; sort?: string; limit?: number; offset?: number } = {}) => {
    const q = new URLSearchParams();
    Object.entries(p).forEach(([k, v]) => v !== undefined && v !== "" && q.set(k, String(v)));
    return request<{ total: number; items: NewsItem[] }>(`/ai/market/news?${q}`);
  },
  parse: (text: string) => request<ParseResult>("/ai/market/parse", { method: "POST", ...json({ text }) }),
  save: (source: "bb_paste" | "manual", entries: Entry[]) =>
    request<{ saved: number; changed: number }>("/ai/market/entries",
      { method: "POST", ...json({ source, entries }) }),
  refresh: () => request<{ results: { job: string; status: string }[] }>(
    "/ai/market/refresh", { method: "POST" }),
};

// --- insights and the morning brief ------------------------------------------

export type Severity = "critical" | "serious" | "warning" | "info";

export interface RateDraftAction {
  type: "prepare_rate_change"; product_code: string; current: string; suggested: string;
  tenor: string; note: string;
}

export interface Evidence { label: string; value: string; unit: "pct" | "bp" | "bdt" | "pct_change" | "date" | "text" }

export interface Insight {
  id: number; kind: string; subject: string; scope: string; severity: Severity;
  title: string; body: string; money_at_stake: number | null; money_basis: string | null;
  evidence: Evidence[]; action: RateDraftAction | null;
  sources: { label: string; url?: string; metric?: string }[];
  business_date: string | null; status: "active" | "resolved";
  raised_at: string; last_seen_at: string; resolved_at: string | null;
  read: boolean; useful: boolean | null; dismissed: boolean;
}

export interface BriefDecision {
  kind: string; subject: string; title: string; severity: Severity;
  money_at_stake: number | null; money_basis: string | null;
  action: RateDraftAction | null; insight_id: number | null;
}

export interface Brief {
  scope: string; scope_label: string; lang: "en" | "bn";
  standard: {
    headline: string;
    sections: { key: string; title: string; lines: string[] }[];
    decisions: BriefDecision[];
    news: { title: string; source: string; url: string; published_at: string | null }[];
    as_of: { today: string; business_date: string | null; market: string | null };
  };
  ai: null | {
    narrative: string; grounded: boolean | null; unverified: string[];
    provider: string | null; model: string | null; created_at: string;
    request_id: number | null; sent: string; current: boolean;
  };
  can_write: boolean;
  can_rewrite: boolean;
}

export const insightApi = {
  list: (p: { status?: "active" | "resolved"; include_dismissed?: boolean } = {}) => {
    const q = new URLSearchParams();
    Object.entries(p).forEach(([k, v]) => v !== undefined && q.set(k, String(v)));
    return request<{ items: Insight[]; unread: number }>(`/ai/insights?${q}`);
  },
  read: (ids: number[] | "all") => request<{ marked: number }>("/ai/insights/read",
    { method: "POST", ...json(ids === "all" ? { all: true } : { ids }) }),
  feedback: (id: number, b: { useful?: boolean | null; dismissed?: boolean }) =>
    request<{ ok: boolean }>(`/ai/insights/${id}/feedback`, { method: "POST", ...json(b) }),
  refresh: () => request<{ status: string }>("/ai/insights/refresh", { method: "POST" }),
  brief: (lang: "en" | "bn") => request<Brief>(`/ai/brief?lang=${lang}`),
  writeBrief: (lang: "en" | "bn", rewrite = false) =>
    request<Brief>("/ai/brief/write", { method: "POST", ...json({ lang, rewrite }) }),
};

/** Taka the way a Bangladeshi banker reads it: crore, lakh, taka. */
export function taka(v: number | string | null | undefined): string {
  if (v == null || v === "") return "—";
  const x = Number(v);
  const a = Math.abs(x);
  const s = x < 0 ? "-" : "";
  if (a >= 1e7) return `${s}৳${(a / 1e7).toFixed(2)} crore`;
  if (a >= 1e5) return `${s}৳${(a / 1e5).toFixed(2)} lakh`;
  return `${s}৳${Math.round(a).toLocaleString("en-IN")}`;
}

/** Hand a proposed benchmark to Rate configuration, which opens its product
 *  form pre-filled. Nothing changes until a person saves that form. */
export const RATE_DRAFT_KEY = "ftp_rate_draft";

export function prepareRateChange(a: RateDraftAction) {
  try {
    sessionStorage.setItem(RATE_DRAFT_KEY, JSON.stringify({
      product_code: a.product_code, benchmark_rate: a.suggested, note: a.note,
    }));
  } catch { /* storage blocked: the form simply opens empty */ }
  location.hash = "#/rates";
}

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
  /** The copilot may chain several lookups with this model. */
  agentic: boolean;
  /** null: decided from the model's name; true/false: an administrator chose. */
  agentic_override: boolean | null;
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
  /** The model actually tested, when the one asked for is no longer offered. */
  model?: string | null;
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
  updateProvider: (id: number, b: Partial<NewProvider> & { agentic?: "auto" | "on" | "off" }) =>
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
  /** Set when the site asked for human verification: reading is paused. */
  blocked?: string | null;
  pages?: Record<string, { found: number; new_or_changed: number; warnings: string[] }> | null;
}

export interface BbRefresh {
  job: string; status: "ok" | "partial" | "blocked" | "failed" | "skipped";
  pages?: Record<string, { found: number; new_or_changed: number; warnings: string[] }>;
  errors?: string[]; blocked?: string; differences?: string[]; error?: string; reason?: string;
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
  /** Read Bangladesh Bank's pages now (treasury desk, head office). */
  refreshBb: () => request<BbRefresh>("/ai/market/refresh-bb", { method: "POST" }),
  refresh: () => request<{ results: { job: string; status: string }[] }>(
    "/ai/market/refresh", { method: "POST" }),
};

// --- insights and the morning brief ------------------------------------------

export type Severity = "critical" | "serious" | "warning" | "info";

export interface RateDraftAction {
  type: "prepare_rate_change"; product_code: string; current: string; suggested: string;
  tenor: string; note: string;
}

/** Open the scenario lab on a what-if built from the finding. */
export interface ScenarioAction { type: "scenario"; label: string; scenario: Record<string, unknown> }

export interface Evidence { label: string; value: string; unit: "pct" | "bp" | "bdt" | "pct_change" | "date" | "text" }

export interface Insight {
  id: number; kind: string; subject: string; scope: string; severity: Severity;
  title: string; body: string; money_at_stake: number | null; money_basis: string | null;
  evidence: Evidence[]; action: RateDraftAction | ScenarioAction | null;
  sources: { label: string; url?: string; metric?: string }[];
  business_date: string | null; status: "active" | "resolved";
  raised_at: string; last_seen_at: string; resolved_at: string | null;
  read: boolean; useful: boolean | null; dismissed: boolean;
}

export interface BriefDecision {
  kind: string; subject: string; title: string; severity: Severity;
  money_at_stake: number | null; money_basis: string | null;
  action: RateDraftAction | ScenarioAction | null; insight_id: number | null;
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

// --- Ask FTP -------------------------------------------------------------------

export interface ResultColumn {
  key: string; label: string;
  unit: "text" | "bdt" | "pct" | "pp" | "date" | "count" | "num" | "mixed" | "mixed_change";
}

export interface ChartSpec {
  type: "bar" | "line" | "cone"; x: string; horizontal?: boolean; stack?: boolean;
  series: ResultColumn[];
  /** For a cone: the history and the forecast band. */
  unit?: string; history?: Point[]; forecast?: Band[];
}

export interface AskResult {
  title: string; description: string; columns: ResultColumn[];
  rows: Record<string, string | number | null>[];
  total: Record<string, string | number | null> | null;
  period: Record<string, string> | null; chart: ChartSpec | null; notes: string[];
  /** Which way each row moved, in words written by code (not by the model). */
  facts?: string[];
  /** The page that shows this in full. */
  link?: { label: string; href: string } | null;
  /** A multi-step answer, as stored: every step's result. */
  steps?: { tool: string; result: AskResult }[];
}

export type AskEvent =
  | { type: "start"; conversation_id: string; sent: string; mode?: "agent" }
  | { type: "status"; text: string }
  | { type: "step"; n: number; tool: string; title: string; thought: string }
  | { type: "step_refused"; n: number; text: string }
  | { type: "result"; result: AskResult; tool: string; step?: number }
  | { type: "followups"; items: string[] }
  | { type: "answer"; text: string | null; grounded: boolean | null; unverified: string[];
      provider?: string; model?: string; explain: boolean; sent: string; note?: string;
      truncated?: boolean;
      /** Where the wording moves a figure the opposite way from the data. */
      conflicts?: string[]; steps?: number }
  | { type: "clarify" | "refused"; text: string }
  | { type: "error"; code: string; text: string }
  | { type: "done"; message_id: number; pinnable: boolean };

export interface StoredMessage {
  id: number; created_at: string; question: string; status: string; lang: string;
  result: AskResult | null; answer: string | null; grounded: boolean | null;
  unverified: string[]; provider: string | null; model: string | null; pinnable: boolean;
}

export interface PinTile { id: number; title: string; question: string; result: AskResult | { error: string } }

const API_BASE = import.meta.env.VITE_API_BASE?.replace(/\/$/, "") || "/api/v1";

/** The page a question is asked on, what it is filtered to, and (coach) its branch. */
export interface AskContext {
  page: string; filters: Record<string, unknown>; branch?: string;
  scenario?: Record<string, unknown>;
  market?: { book: string; peers: string; product?: string; bank?: string; basis?: string };
}

/** A plan built by a page (a "Why?" button), with the page's filters. */
export interface AskPreset { plan: Record<string, unknown>; filters?: Record<string, unknown> }

export const askApi = {
  /** POST a question and hand each streamed event to `on` as it arrives. */
  ask: async (body: { question: string; conversation_id?: string | null; lang: "en" | "bn";
                      preset?: AskPreset; context?: AskContext },
              on: (e: AskEvent) => void, signal?: AbortSignal) => {
    const token = localStorage.getItem("ftp_token");
    const res = await fetch(`${API_BASE}/ai/ask`, {
      method: "POST", signal, body: JSON.stringify(body),
      headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    });
    if (!res.ok || !res.body) {
      const t = await res.text().catch(() => "");
      let msg = res.statusText;
      try { const d = JSON.parse(t).detail; msg = typeof d === "string" ? d : d?.message ?? msg; } catch { /* not JSON */ }
      throw new ApiError(res.status, msg);
    }
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let nl: number;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl).trim();
        buf = buf.slice(nl + 1);
        if (line) on(JSON.parse(line) as AskEvent);
      }
    }
  },
  suggestions: (page?: string) =>
    request<{ items: string[] }>(`/ai/ask/suggestions${page ? `?page=${encodeURIComponent(page)}` : ""}`),
  conversations: () => request<{ items: { id: string; title: string; updated_at: string }[] }>("/ai/conversations"),
  conversation: (id: string) => request<{ id: string; title: string; messages: StoredMessage[] }>(`/ai/conversations/${id}`),
  deleteConversation: (id: string) => request<void>(`/ai/conversations/${id}`, { method: "DELETE" }),
  pins: () => request<{ items: PinTile[] }>("/ai/pins"),
  pin: (message_id: number) => request<{ id: number; title: string }>("/ai/pins", { method: "POST", ...json({ message_id }) }),
  unpin: (id: number) => request<void>(`/ai/pins/${id}`, { method: "DELETE" }),
};

// --- Branch coach and upload check --------------------------------------------

export interface CoachMetric {
  key: string; label: string; unit: "bdt" | "pct" | "share"; higher_is_better: boolean;
  value: number | null; before: number | null; district_median: number | null;
  bank_median: number | null; district_rank: [number, number] | null;
  bank_rank: [number, number] | null; better: boolean | null;
}

export interface CoachView {
  branch: { code: string; name: string; district: string | null };
  peers: { scope: "district" | "division"; name: string; count: number };
  period: { start: string; end: string; prior_start: string; prior_end: string };
  rank: { profit: [number, number] | null; profit_before: number | null; yield: [number, number] | null };
  metrics: CoachMetric[];
  actions: { key: string; title: string; body: string; money: number | null; basis: string; metric: string }[];
  strengths: string[];
}

export interface CoachNote {
  text: string; grounded: boolean | null; unverified: string[];
  provider: string; model: string; sent: string; truncated?: boolean;
}

export const coachApi = {
  branches: () => request<{ items: { code: string; name: string; district: string }[] }>("/ai/coach/branches"),
  get: (code: string) => request<CoachView>(`/ai/coach/${encodeURIComponent(code)}`),
  note: (code: string, lang: "en" | "bn") =>
    request<CoachNote>(`/ai/coach/${encodeURIComponent(code)}/note`, { method: "POST", ...json({ lang }) }),
};

export interface UploadReview {
  batch_ref: string; status: string; headline: string; serious: number; warnings: number;
  rules: { rule: string; severity: "REJECT" | "WARN" | "INFO"; findings: number; rows: number;
           title: string; meaning: string; fix: string; link: { label: string; href: string } | null;
           values: { value: string; count: number }[];
           examples: { where: string; message: string }[] }[];
  days: { date: string; compared_with: string | null; note: string | null;
          findings: { severity: "serious" | "warning" | "info"; kind: string; branch: string | null;
                      title: string; detail: string }[] }[];
  screened_dates: number; more_dates: boolean;
}

export const uploadCheckApi = {
  review: (batchRef: string) => request<UploadReview>(`/ai/uploads/${encodeURIComponent(batchRef)}/review`),
};

// --- Outlook: forecasts, the policy rate, the market's posted rates -----------

export interface Band { date: string; p10: number; p50: number; p90: number }
export interface Point { date: string; value: number }

export interface MarketForecast {
  code: string; name: string; short: string; unit: string; category: string;
  last: Point; history: Point[]; forecast: Band[]; in_30d: Band | null; in_90d: Band | null;
  confidence: "high" | "medium" | "low"; confidence_reason: string; notes: string[];
  spacing_days: number; points: number; bounded_by: string | null;
  backtest: { mae: number | null; mape: number | null; coverage: number | null; horizon_steps: number };
}

export interface PolicyDriver {
  key: string; label: string; value: string; score: number; weight: number; push: number; explain: string;
}

export interface PolicyOutlook {
  leaning: "hike" | "hold" | "cut"; score: number; odds: { hike: number; hold: number; cut: number };
  repo: number | null; next_meeting: string | null; next_meeting_basis: string;
  implied_91d_in_9m: number | null; missing: string[]; drivers: PolicyDriver[]; note: string;
}

export interface BookMetric {
  metric: string; label: string; unit: "bdt" | "pct"; kind: "stock" | "flow" | "rate";
  history: Point[]; forecast: Band[]; last: Point;
  confidence: "high" | "medium" | "low"; confidence_reason: string; notes: string[]; points: number;
  backtest: { mae: number | null; mape: number | null; coverage: number | null };
  month: (Partial<Band> & { end?: string; so_far?: number; previous_month?: number | null }) | null;
  quarter: (Partial<Band> & { end?: string; so_far?: number }) | null;
  in_90d?: Band | null;
}

export interface BookOutlook {
  available: boolean; reason?: string; latest: string; month_end: string; quarter_end: string;
  horizon_end: string; label: string; days_of_history: number; metrics: BookMetric[];
}

export interface PeerRow {
  product_code: string; name: string; side: "ASSET" | "LIABILITY"; peer_book: string;
  peer_product: string; peer_label: string; our_rate: number; market_median: number;
  pcb_median: number | null; p25: number | null; p75: number | null; gap: number; balance: number;
  self_posted: number | null; month: string | null; as_of: string;
}

export interface PeerTable {
  month: string | null; book: string; self_bank: string; collected: string | null;
  products: { product: string; label: string; banks: number; median: number | null; p25: number | null;
              p75: number | null; min: number | null; max: number | null; self: number | null;
              rank: number | null; pcb_median: number | null; self_range: [number, number] | null }[];
  banks: { bank: string; group: string; rates: Record<string, number | null> }[];
}

export interface MacroSeries {
  code: string; name: string; unit: string;
  actual: { year: number; value: number }[]; projection: { year: number; value: number }[];
}

export interface TrackRecord {
  summary: { scored: number; inside_band: number; hit_rate: number | null; pending: number };
  items: { scope: string; target: string; target_date: string; made_on: string; unit: string;
           p10: number; p50: number; p90: number; actual: number | null; inside: boolean | null;
           error: number | null; confidence: string }[];
}

export const outlookApi = {
  market: () => request<{ series: MarketForecast[]; horizon_days: number }>("/ai/outlook/market"),
  policy: () => request<PolicyOutlook>("/ai/outlook/policy"),
  book: (branch?: string) =>
    request<BookOutlook>(`/ai/outlook/book${branch ? `?branch=${encodeURIComponent(branch)}` : ""}`),
  trackRecord: () => request<TrackRecord>("/ai/outlook/track-record"),
  bookVsPeers: () => request<{ items: PeerRow[]; self_bank: string; label: string }>("/ai/public/book-vs-peers"),
  peers: (book: "deposit" | "lending") => request<PeerTable>(`/ai/public/peers?book=${book}`),
  macro: () => request<{ series: MacroSeries[]; last_mpc: string | null }>("/ai/public/macro"),
  refreshPublic: () => request<Record<string, unknown>>("/ai/public/refresh", { method: "POST" }),
};

// --- Scenario lab ---------------------------------------------------------------

export interface ScenarioParams {
  market_bp: number; bench_follow: number; bench_follow_demand: number; deposit_pass: number;
  demand_pass: number; loan_pass: number; competitor_bp: number;
  product_bench_bp: Record<string, number>; product_rate_bp: Record<string, number>;
  deposit_growth_pct: number; loan_growth_pct: number; elasticity: number;
  branches: string[]; horizon_months: number;
}

export interface ScenarioTotals {
  deposits: number; advances: number; branch_ftp: number; customer_nii: number;
  surplus_income: number; bank_nii: number; treasury: number; nim: number | null;
}

export interface ScenarioGroup {
  key: string; label: string; side: string; ftp_base: number; ftp_new: number; nii_base: number;
  nii_new: number; balance_base: number; balance_new: number; ftp_change: number; nii_change: number;
}

export interface ScenarioResult {
  available: boolean; reason?: string; scenario: ScenarioParams; days: number;
  market_rate: number; market_rate_label: string;
  base: ScenarioTotals; scenario_totals: ScenarioTotals; change: ScenarioTotals;
  waterfall: { key: string; label: string; value: number }[];
  by_product: ScenarioGroup[]; by_branch: ScenarioGroup[];
  path: { day: number; branch_ftp_per_day: number; bank_nii_per_day: number }[];
  base_window: { latest: string; start: string; days: number }; lines: number;
}

export interface ScenarioPreset { key: string; label: string; why: string; scenario: ScenarioParams }

export const scenarioApi = {
  presets: () => request<{ items: ScenarioPreset[]; defaults: ScenarioParams }>("/ai/scenario/presets"),
  run: (scenario: ScenarioParams) =>
    request<ScenarioResult>("/ai/scenario/run", { method: "POST", ...json({ scenario }) }),
  parse: (text: string) =>
    request<{ scenario: ScenarioParams; title: string; assumptions: string[]; provider: string; model: string }>(
      "/ai/scenario/parse", { method: "POST", ...json({ text }) }),
  saved: () => request<{ items: { id: number; name: string; scenario: ScenarioParams;
                                  summary: Record<string, number>; created_at: string }[] }>("/ai/scenario/saved"),
  save: (name: string, scenario: ScenarioParams, summary: Record<string, number>) =>
    request<{ id: number }>("/ai/scenario/saved", { method: "POST", ...json({ name, scenario, summary }) }),
  remove: (id: number) => request<void>(`/ai/scenario/saved/${id}`, { method: "DELETE" }),
};

// --- The bank's pulse -------------------------------------------------------------

export interface PulseItem {
  id: number; title: string; severity: Severity; money: number | null; basis: string | null;
  action: RateDraftAction | ScenarioAction | null;
}

export interface Pulse {
  available: boolean; reason?: string; label: string; as_of: string; score: number; grade: string;
  parts: { key: string; label: string; score: number; value: string; explain: string; weight: number }[];
  versus: { label: string; ours: number | null; industry: number | null; better: "lower" | "higher" }[];
  industry_month: string | null; outlook: BookOutlook | null;
  risks: PulseItem[]; openings: PulseItem[];
  ratios: Record<string, number | null>;
}

export const pulseApi = { get: () => request<Pulse>("/ai/pulse") };

// --- The ALCO pack ----------------------------------------------------------------

export interface AlcoPack {
  label: string; prepared: string; policy: PolicyOutlook; market: MarketForecast[];
  book: BookOutlook | null;
  sensitivity: { label: string; bank_nii: number; branch_ftp: number; treasury: number;
                 deposits: number; nim: number | null }[];
  pricing: PeerRow[]; benchmarks: BenchmarkRow[];
  decisions: { id: number; title: string; severity: Severity; money: number | null; basis: string | null }[];
  macro: MacroSeries[];
}

export const alcoApi = {
  get: () => request<AlcoPack>("/ai/alco"),
  commentary: () => request<{ text: string; grounded: boolean | null; unverified: string[];
                              provider: string; model: string; truncated: boolean; sent: string }>(
    "/ai/alco/commentary", { method: "POST" }),
};

// --- Market rate explorer ---------------------------------------------------------

export type PeerSet = "competitors" | "pcb" | "fb" | "scb" | "islamic" | "all";
export type RateBook = "deposit" | "lending";
/** best: each bank's best posted offer (highest deposit, lowest loan rate); typical: the middle of its range. */
export type RateBasis = "best" | "typical";

export interface MarketHit {
  type: "bank" | "category" | "product"; label: string; detail: string;
  code?: string; book?: RateBook | null; product?: string | null;
}

export interface MarketGrid {
  book: RateBook; month: string | null; months?: string[]; peers: PeerSet; peer_label: string;
  self_bank: string; basis: RateBasis;
  categories: { product: string; label: string; median: number | null; p25: number | null; p75: number | null;
                rank: number | null; banks: number; self: number | null; peer_median: number | null;
                book: { rate: number | null; balance: number; products: string[] } | null }[];
  banks: { code: string; name: string; group: string; islamic: boolean; self: boolean; in_set: boolean;
           rates: Record<string, number | null>; ranges: Record<string, [number, number] | null> }[];
}

export interface MarketCategory {
  available: boolean; book: RateBook; product: string; label: string; month: string; peers: PeerSet;
  peer_label: string; self_bank: string;
  standing: { median: number | null; p25: number | null; p75: number | null; min: number | null;
              max: number | null; self: number | null; rank: number | null; banks: number;
              percentile: number | null; peer_median: number | null };
  banks: { code: string; name: string; group: string; low: number; high: number; mid: number;
           self: boolean; in_set: boolean }[];
  trend: { month: string; median: number | null; peer_median: number | null; self: number | null }[];
  book_rate: { rate: number | null; balance: number; products: string[] } | null;
}

export interface MarketBank {
  available: boolean; code: string; name: string; group: string; group_label: string; islamic: boolean;
  self_bank: string;
  books: Partial<Record<RateBook, { month: string; rows: { product: string; label: string; low: number;
    high: number; mid: number; change: number | null; median: number | null; our_posted: number | null;
    our_book: number | null; gap_to_us: number | null }[] }>>;
}

export interface OurProduct extends PeerRow {
  mapping: "auto" | "set"; peer_median: number | null; peer_label_set: string;
  gap_to_peers: number | null; new: boolean;
}

export const marketRatesApi = {
  search: (q: string) => request<{ items: MarketHit[] }>(`/ai/market/search?q=${encodeURIComponent(q)}`),
  grid: (book: RateBook, peers: PeerSet, basis: RateBasis) =>
    request<MarketGrid>(`/ai/market/grid?book=${book}&peers=${peers}&basis=${basis}`),
  category: (book: RateBook, product: string, peers: PeerSet, basis: RateBasis) =>
    request<MarketCategory>(`/ai/market/category?book=${book}&product=${product}&peers=${peers}&basis=${basis}`),
  bank: (code: string, basis: RateBasis) =>
    request<MarketBank>(`/ai/market/bank/${encodeURIComponent(code)}?basis=${basis}`),
  movers: (peers: PeerSet) => request<{ available: boolean; note: string | null; peer_label: string;
    items: { book: RateBook; product: string; label: string; code: string; name: string; from: number;
             to: number; change_bp: number; month: string }[] }>(`/ai/market/movers?peers=${peers}`),
  products: () => request<{ items: OurProduct[]; label: string; self_bank: string;
    unmapped: { product_code: string; name: string; side: string; mapping: string }[];
    categories: { value: string; label: string }[];
    competitors: { code: string; name: string }[];
    banks: { code: string; name: string; group: string }[] }>("/ai/market/products"),
  setProductMap: (product_code: string, category: string | null) =>
    request<{ product_map: Record<string, string> }>("/ai/market/product-map",
      { method: "PUT", ...json({ product_code, category }) }),
  setCompetitors: (banks: string[]) =>
    request<{ competitors: string[] }>("/ai/market/competitors", { method: "PUT", ...json({ banks }) }),
};

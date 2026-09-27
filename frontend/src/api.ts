/** Typed client for the FTP API. */

/** Same-origin by default; an absolute URL when the API is on another host.
 *
 *  Same-origin is the better arrangement -- no preflight, no CORS to keep in
 *  step -- but it needs a proxy in front, which not every host provides. The
 *  override exists so the client does not depend on one. */
const BASE = import.meta.env.VITE_API_BASE?.replace(/\/$/, "") || "/api/v1";

let token: string | null = localStorage.getItem("ftp_token");

export function setToken(t: string | null) {
  token = t;
  if (t) localStorage.setItem("ftp_token", t);
  else localStorage.removeItem("ftp_token");
}
export const hasToken = () => Boolean(token);

export class ApiError extends Error {
  constructor(public status: number, message: string, public body?: unknown) {
    super(message);
  }
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      ...(init.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init.headers ?? {}),
    },
  });
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  const body = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const detail = body?.detail;
    throw new ApiError(
      res.status,
      typeof detail === "string" ? detail : detail?.message ?? res.statusText,
      body,
    );
  }
  return body as T;
}

/** Filters are shared by every dashboard and analytics endpoint, which is what
 *  lets one filter row above the page scope all of them. */
export interface Filters {
  date_from?: string;
  date_to?: string;
  branch_id?: number[];
  division_id?: number;
  district_id?: number;
  branch_category?: string;
  product_code?: string[];
  side?: "ASSET" | "LIABILITY";
  account_no?: string;
  ftp_sign?: "POSITIVE" | "NEGATIVE";
}

export function qs(f: Filters, extra: Record<string, unknown> = {}) {
  const p = new URLSearchParams();
  const put = (k: string, v: unknown) => {
    if (v === undefined || v === null || v === "") return;
    if (Array.isArray(v)) v.forEach((x) => p.append(k, String(x)));
    else p.append(k, String(v));
  };
  Object.entries(f).forEach(([k, v]) => put(k, v));
  Object.entries(extra).forEach(([k, v]) => put(k, v));
  const s = p.toString();
  return s ? `?${s}` : "";
}

// --- auth ------------------------------------------------------------------

export interface Me {
  id: number; username: string; full_name: string;
  scope_level: "HO" | "DIVISION" | "DISTRICT" | "BRANCH";
  scope_id: number | null; scope_label: string;
  roles: string[]; permissions: string[];
  must_change_password: boolean;
}

export const api = {
  login: (username: string, password: string) =>
    request<{ access_token: string; must_change_password: boolean }>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  me: () => request<Me>("/auth/me"),
  changePassword: (current_password: string, new_password: string) =>
    request<void>("/auth/password/change", {
      method: "POST",
      body: JSON.stringify({ current_password, new_password }),
    }),

  // --- dashboard ----------------------------------------------------------
  kpis: (f: Filters) => request<Kpis>(`/dashboard/kpis${qs(f)}`),
  byBranch: (f: Filters) => request<Series[]>(`/dashboard/by-branch${qs(f)}`),
  byProduct: (f: Filters) => request<Series[]>(`/dashboard/by-product${qs(f)}`),
  byCategory: (f: Filters) => request<Series[]>(`/dashboard/by-category${qs(f)}`),
  byDivision: (f: Filters) => request<Series[]>(`/dashboard/by-division${qs(f)}`),
  byDistrict: (f: Filters) => request<Series[]>(`/dashboard/by-district${qs(f)}`),
  heatmap: (f: Filters) => request<HeatCell[]>(`/dashboard/heatmap${qs(f)}`),
  accounts: (f: Filters, o: Record<string, unknown>) =>
    request<PageOf<AccountRow>>(`/dashboard/accounts${qs(f, o)}`),

  // --- analytics ----------------------------------------------------------
  summary: (f: Filters) => request<Summary>(`/analytics/summary${qs(f)}`),
  trend: (f: Filters, ma = 7) => request<Trend>(`/analytics/trend${qs(f, { ma_window: ma })}`),
  bridge: (f: Filters, by: Dim) => request<Bridge>(`/analytics/variance-bridge${qs(f, { by })}`),
  waterfall: (f: Filters, by?: Dim) =>
    request<Waterfall>(`/analytics/spread-waterfall${qs(f, by ? { by } : {})}`),
  concentration: (f: Filters, by: Dim) =>
    request<Concentration>(`/analytics/concentration${qs(f, { by })}`),
  rankings: (f: Filters, by: Dim) => request<Rankings>(`/analytics/rankings${qs(f, { by })}`),
  distribution: (f: Filters, buckets = 12) =>
    request<Distribution>(`/analytics/rate-distribution${qs(f, { buckets })}`),
  outliers: (f: Filters, z = 3) => request<Outliers>(`/analytics/outliers${qs(f, { z })}`),
  leakage: (f: Filters) => request<Leakage>(`/analytics/leakage${qs(f)}`),
  accountRisk: (f: Filters, by: Dim) =>
    request<AccountRisk>(`/analytics/account-risk${qs(f, { by })}`),
  scatter: (f: Filters, by: Dim) => request<Scatter>(`/analytics/scatter${qs(f, { by })}`),
  balanceSheet: (f: Filters) => request<BalanceSheet>(`/analytics/balance-sheet${qs(f)}`),
  headlinePerformers: (f: Filters) =>
    request<HeadlinePerformers>(`/analytics/headline-performers${qs(f)}`),
  leaderboard: (f: Filters, group: Dim, of: Dim, top = 3, metric: "profit" | "yield" = "profit") =>
    request<Leaderboard>(`/analytics/leaderboard${qs(f, { group, of, top, metric })}`),
  productLeadership: (f: Filters, area: Dim) =>
    request<ProductLeadership>(`/analytics/product-leadership${qs(f, { area })}`),
  dataVersion: () => request<DataVersion>("/system/data-version"),
  nii: (f: Filters) => request<Nii>(`/analytics/nii-reconciliation${qs(f)}`),
  ratios: (f: Filters) => request<Ratios>(`/analytics/banking-ratios${qs(f)}`),
  depositCost: (f: Filters) => request<DepositCost>(`/analytics/deposit-cost${qs(f)}`),
  advanceYield: (f: Filters) => request<AdvanceYield>(`/analytics/advance-yield${qs(f)}`),
  repricing: (f: Filters) => request<Repricing>(`/analytics/repricing${qs(f)}`),
  watchlist: (f: Filters) => request<Watchlist>(`/analytics/watchlist${qs(f)}`),
  periodSummary: (f: Filters) => request<PeriodSummary>(`/analytics/period-summary${qs(f)}`),
  leakage2: (f: Filters) => request<Leakage>(`/analytics/leakage${qs(f)}`),

  // --- master -------------------------------------------------------------
  branches: (includeInactive = false) =>
    request<Branch[]>(`/branches${qs({}, { include_inactive: includeInactive })}`),
  divisions: () => request<Node_[]>("/divisions"),
  districts: () => request<Node_[]>("/districts"),
  products: (includeInactive = false) =>
    request<Product[]>(`/products${qs({}, { include_inactive: includeInactive })}`),
  createProduct: (p: NewProduct) =>
    request<Product>("/products", { method: "POST", body: JSON.stringify(p) }),
  updateProduct: (code: string, b: Partial<NewProduct> & { is_active?: boolean }) =>
    request<Product>(`/products/${code}`, { method: "PATCH", body: JSON.stringify(b) }),
  deleteProduct: (code: string) =>
    request<{ deleted: boolean }>(`/products/${code}`, { method: "DELETE" }),
  branchUsage: (code: string) => request<BranchUsage>(`/branches/${code}/usage`),
  createBranch: (b: NewBranch) =>
    request<Branch>("/branches", { method: "POST", body: JSON.stringify(b) }),
  updateBranch: (code: string, b: Partial<NewBranch> & { is_active?: boolean }) =>
    request<Branch>(`/branches/${code}`, { method: "PATCH", body: JSON.stringify(b) }),
  deleteBranch: (code: string) =>
    request<{ deleted: boolean }>(`/branches/${code}`, { method: "DELETE" }),
  deactivateBranch: (code: string) =>
    request<Branch>(`/branches/${code}/deactivate`, { method: "POST" }),
  reactivateBranch: (code: string) =>
    request<Branch>(`/branches/${code}/reactivate`, { method: "POST" }),

  // --- rate configuration -------------------------------------------------
  /** The global defaults in force on a date. 404 when none is, which is a
   *  configuration fault rather than an empty state. */
  globalConfig: (on?: string) =>
    request<GlobalConfig>(`/config/global${qs({}, on ? { on } : {})}`),
  globalConfigHistory: (limit = 100) =>
    request<GlobalConfig[]>(`/config/global/history${qs({}, { limit })}`),
  /** Change the defaults from a date, keeping the superseded version intact. */
  setGlobalConfig: (b: GlobalConfigUpdate) =>
    request<GlobalConfig>("/config/global", {
      method: "PUT", body: JSON.stringify(b),
    }),
  /** Fix the current version in place. 409 once it has priced anything. */
  correctGlobalConfig: (b: GlobalConfigUpdate) =>
    request<GlobalConfig>("/config/global", {
      method: "PATCH", body: JSON.stringify(b),
    }),
  /** What the engine would use for a product, and which layer supplied each
   *  component -- the view that makes the override model legible. */
  productRates: (code: string, on?: string) =>
    request<ProductRates>(`/products/${code}/rates${qs({}, on ? { on } : {})}`),
  setProductRate: (code: string, b: ProductRateUpdate) =>
    request<{ product_code: string; version: number; benchmark_rate: string;
              effective_from: string }>(
      `/products/${code}/rates`, { method: "POST", body: JSON.stringify(b) }),
  /** Every product rate version, superseded ones included; one product or all. */
  productRateHistory: (code?: string, limit = 500) =>
    request<ProductRateVersion[]>(
      `/products/${code ? `${code}/` : ""}rates/history${qs({}, { limit })}`),
  /** Uploaded dates still priced on rates that a backdated change superseded. */
  staleDates: () => request<StaleDate[]>("/config/stale-dates"),
  /** Keep those dates' published figures; new uploads use the new rates. */
  keepDates: (dates: string[], reason?: string) =>
    request<{ dates_kept: string[] }>("/config/keep", {
      method: "POST", body: JSON.stringify({ dates, reason }),
    }),
  /** Restate those dates on the rates now in force. */
  recalculateDates: (dates: string[], reason?: string) =>
    request<{ run_ref: string; dates_recalculated: string[]; rows: number }>(
      "/config/recalculate", {
        method: "POST", body: JSON.stringify({ dates, reason }),
      }),

  // --- audit trail --------------------------------------------------------
  auditLog: (f: AuditFilters = {}) =>
    request<PageOf<AuditEntry>>(`/audit${qs({}, { ...f })}`),
  auditEntry: (id: number) => request<AuditEntryDetail>(`/audit/${id}`),

  // --- uploads ------------------------------------------------------------
  probe: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return request<Probe>("/uploads/probe", { method: "POST", body: fd });
  },
  /** `business_date` is required and operator-entered; the server refuses
   *  without it rather than falling back to the sheet name. */
  upload: (file: File, opts: {
    business_date: string; sheet_name?: string; mode?: "replace" | "merge";
  }) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("business_date", opts.business_date);
    if (opts.sheet_name) fd.append("sheet_name", opts.sheet_name);
    fd.append("mode", opts.mode ?? "replace");
    return request<UploadResult>("/uploads", { method: "POST", body: fd });
  },
  deletionImpact: (ref: string) =>
    request<DeletionImpact>(`/uploads/${ref}/deletion-impact`),
  deleteBatch: (ref: string, reason?: string) =>
    request<DeleteResult>(
      `/uploads/${ref}${qs({}, reason ? { reason } : {})}`, { method: "DELETE" }),
  /** The rejected rows, as a workbook that goes straight back in once the
   *  master data is fixed. Downloaded via fetch so the auth header travels
   *  with it -- a plain link would be unauthenticated. */
  downloadRejects: async (ref: string) => {
    const res = await fetch(`${BASE}/uploads/${ref}/rejects.xlsx`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!res.ok) throw new ApiError(res.status, "could not build the rejects file");
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `rejected-rows-${ref}.xlsx`;
    a.click();
    URL.revokeObjectURL(url);
  },
  batches: () => request<Batch[]>("/uploads"),
  batchExceptions: (ref: string) => request<ExceptionRow[]>(`/uploads/${ref}/exceptions`),
};

// --- types -----------------------------------------------------------------

export type Dim = "branch" | "product" | "category" | "division" | "district";
export type Num = string; // Decimals cross the wire as strings to keep precision.

export interface Kpis {
  asset_balance: Num; liability_balance: Num; total_balance: Num;
  interest_receivable: Num; interest_payable: Num;
  asset_ftp_profit: Num; liability_ftp_profit: Num; net_ftp_profit: Num;
  ftp_over_balance_pct: Num;
  branch_count: number; product_count: number; account_count: number;
  day_count: number; negative_ftp_count: number;
  as_of: string | null;
}

export interface Delta {
  current: Num; prior: Num; change: Num; change_pct: Num | null;
}
export interface Summary extends Kpis {
  comparison: {
    current_period: { start: string; end: string; days: number };
    prior_period: { start: string; end: string; days: number };
    prior_has_data: boolean;
    deltas: Record<string, Delta>;
  } | null;
}

export interface Series {
  key: string | number; label: string;
  /**
   * The bare code behind the label, where the level has one. `label` is
   * "<code> <name>"; a chart axis only has room for the code.
   */
  code?: string | null;
  /** The level above: a district's division, a branch's district. */
  parent_label?: string | null;
  asset_ftp_profit: Num; liability_ftp_profit: Num; net_ftp_profit: Num;
  asset_balance: Num; liability_balance: Num; total_balance: Num;
  account_count: number; negative_ftp_count: number; avg_ftp_rate: Num;
}

export interface TrendPoint extends Series {
  moving_average: Num; cumulative: Num;
  day_over_day: Num | null; day_over_day_pct: Num | null;
}
export interface Trend {
  points: TrendPoint[];
  summary: {
    days: number; total: Num; mean_daily: Num; stdev_daily: Num;
    volatility_pct: Num | null;
    best_day: { date: string; value: Num };
    worst_day: { date: string; value: Num };
    annualised_run_rate: Num;
  } | null;
}

export interface BridgeSegment {
  label: string; status: "new" | "closed" | "continuing";
  prior_profit: Num; current_profit: Num; change: Num;
  volume_effect: Num; rate_effect: Num; interaction_effect: Num;
  prior_rate: Num; current_rate: Num;
  prior_balance: Num; current_balance: Num;
}
export interface Bridge {
  available: boolean; reason?: string; dimension?: Dim;
  comparison_mode?: "preceding_period" | "split_window";
  current_period?: { start: string; end: string; days: number };
  prior_period?: { start: string; end: string; days: number };
  opening_profit?: Num; closing_profit?: Num; total_change?: Num;
  volume_effect?: Num; rate_effect?: Num; interaction_effect?: Num;
  residual?: Num; segments?: BridgeSegment[];
}

export interface Waterfall {
  components: { key: string; label: string; amount: Num; rate: Num }[];
  net_ftp_profit: Num; total_balance: Num; check: Num;
  by_segment: {
    label: string; benchmark: Num; customer_rate: Num;
    liquidity: Num; other: Num; net: Num;
  }[] | null;
}

export interface Concentration {
  available: boolean; dimension?: Dim; segment_count?: number;
  net_ftp_profit?: Num; hhi?: Num; hhi_interpretation?: string;
  top_1_pct?: Num; top_3_pct?: Num; top_5_pct?: Num;
  segments_to_80pct?: number;
  segments: { rank: number; label: string; net_ftp_profit: Num;
    share_pct: Num; cumulative_pct: Num; total_balance: Num;
    account_count: number }[];
}

export interface RankRow {
  rank: number; label: string; net_ftp_profit: Num; total_balance: Num;
  yield_pct: Num; account_count: number; negative_ftp_count: number;
  profit_per_account: Num; percentile: Num; quartile: number;
}
export interface Rankings {
  available: boolean; dimension?: Dim; median_yield_pct?: Num;
  best?: RankRow; worst?: RankRow; spread_pct?: Num; rows: RankRow[];
}

export interface Distribution {
  available: boolean; min_rate?: Num; max_rate?: Num;
  buckets: { bucket: number; from_rate: Num; to_rate: Num;
    account_count: number; account_pct: Num; balance: Num;
    balance_pct: Num; ftp_income: Num; is_negative: boolean }[];
}

export interface Outliers {
  z_threshold: number; count: number;
  rows: { business_date: string; branch_code: string; account_no: string;
    product_code: string; side: string; balance: Num; normalized_roi: Num;
    ftp_rate: Num; ftp_income: Num; z_rate: Num; z_roi: Num }[];
}

export interface AccountRisk {
  dimension: Dim;
  available: boolean;
  totals: { account_days: number; negative_days: number; drag: Num;
            negative_balance: Num; balance: Num };
  segments: {
    label: string;
    account_days: number; negative_days: number; loss_rate_pct: Num;
    balance: Num; negative_balance: Num; balance_at_risk_pct: Num;
    ftp_income: Num; drag: Num;
  }[];
}

export interface Leakage {
  negative_account_days: number; negative_balance: Num; drag: Num;
  total_ftp_profit: Num; drag_as_pct_of_profit: Num | null; opportunity: Num;
  by_product: { label: string; account_count: number; balance: Num; ftp_income: Num }[];
  by_branch: { label: string; account_count: number; balance: Num; ftp_income: Num }[];
  worst_accounts: { business_date: string; branch_code: string; account_no: string;
    product_code: string; balance: Num; normalized_roi: Num;
    ftp_rate: Num; ftp_income: Num }[];
}

export interface Scatter {
  available: boolean; dimension?: Dim;
  median_balance?: Num; median_yield_pct?: Num;
  points: { label: string; balance: Num; ftp_income: Num; yield_pct: Num;
    account_count: number; quadrant: string }[];
}

export interface BalanceSheet {
  asset: SideStats | null; liability: SideStats | null;
  funding_gap: Num; coverage_ratio: Num | null; ftp_spread: Num;
}
export interface SideStats {
  balance: Num; ftp_profit: Num; avg_customer_rate: Num;
  avg_ftp_rate: Num; account_days: number;
}

export interface HeatCell {
  branch_code: string; product_code: string;
  net_ftp_profit: Num; total_balance: Num;
}

export interface AccountRow {
  business_date: string; branch_code: string; account_no: string;
  product_code: string; side: string; balance: Num; normalized_roi: Num;
  roi_source: string; benchmark_rate: Num; liquidity_cost: Num;
  other_cost: Num; ftp_rate: Num; ftp_income: Num;
  customer_interest: Num; asset_ftp_profit: Num; liability_ftp_profit: Num;
  negative_ftp_flag: boolean;
}
export interface PageOf<T> { items: T[]; total: number; limit: number; offset: number }

export interface Branch {
  id: number; branch_code: string; branch_name: string;
  division_id: number | null; district_id: number;
  category: string; opened_on: string | null; is_active: boolean;
  udf: Record<string, unknown> | null;
}
export interface NewBranch {
  branch_code: string; branch_name: string; district_code: string;
  category: string; opened_on?: string | null;
}
export interface BranchUsage {
  branch_code: string; fact_rows: number; bank_rows: number;
  aggregate_rows: number; deletable: boolean; note: string;
}
export interface Node_ { id: number; code: string; name: string;
  division_id?: number; is_active: boolean }
export interface Product {
  id: number; product_code: string; short_name: string; details: string | null;
  side: string; liability_nature: string | null; is_active: boolean;
}
export interface NewProduct {
  product_code: string; short_name: string; side: "ASSET" | "LIABILITY";
  benchmark_rate?: string; liability_nature?: "DEMAND" | "TIME" | null;
  details?: string | null;
}

export type DayCountBasis = "ACT_365" | "ACT_360";

/** One version of the global rate defaults.
 *
 *  `benchmark_rate` is nullable on purpose: null means the global layer offers
 *  no benchmark, so every product must carry its own override. That is what
 *  turns a missing benchmark into a hard error instead of a silent zero. */
export interface GlobalConfig {
  version: number;
  benchmark_rate: Num | null;
  liquidity_cost: Num | null;
  other_cost: Num | null;
  day_count_basis: DayCountBasis;
  effective_from: string;
  effective_to: string | null;
  /** APPROVED, or SUPERSEDED once a backdated version replaced it. */
  status: string;
  note: string | null;
  /** Completed runs and rows this version priced. Non-zero runs is why the UI
   *  offers a new version rather than an in-place correction. */
  runs_priced: number;
  rows_priced: number;
  editable_in_place: boolean;
}

/** Only the fields supplied are changed; the rest carry forward. */
export interface GlobalConfigUpdate {
  benchmark_rate?: string | null;
  liquidity_cost?: string;
  other_cost?: string;
  day_count_basis?: DayCountBasis;
  effective_from?: string;
  note?: string | null;
}

export interface ProductRates {
  product_code: string;
  benchmark_rate: Num | null; benchmark_source: string;
  liquidity_cost: Num; liquidity_source: string;
  other_cost: Num; other_source: string;
  effective_on: string;
}

/** `null` on a component means inherit the global default; `"0"` is an
 *  explicit zero override. The two are not the same thing. */
export interface ProductRateUpdate {
  benchmark_rate: string;
  liquidity_cost?: string | null;
  other_cost?: string | null;
  effective_from?: string;
  note?: string | null;
}

/** One effective-dated product rate version. `null` liquidity or other cost
 *  means the version inherits the global default. */
export interface ProductRateVersion {
  product_code: string;
  short_name: string;
  version: number;
  benchmark_rate: Num | null;
  liquidity_cost: Num | null;
  other_cost: Num | null;
  effective_from: string;
  effective_to: string | null;
  status: string;
  changed_by: string | null;
  changed_at: string | null;
  note: string | null;
  rows_priced: number;
}

export interface StaleDate {
  business_date: string;
  products: string[];
  rows: number;
  /** Set when the rates for the date cannot be resolved at all. */
  blocked_by: string | null;
  /** Deliberately kept on the earlier rates rather than recalculated. */
  kept: boolean;
  kept_by: string | null;
  kept_at: string | null;
}

export interface AuditFilters {
  entity_type?: string[];
  entity_id?: string;
  action?: string[];
  actor?: string;
  date_from?: string;
  date_to?: string;
  limit?: number;
  offset?: number;
}

export interface AuditEntry {
  id: number;
  occurred_at: string;
  actor_username: string | null;
  actor_scope: string | null;
  action: string;
  entity_type: string;
  entity_id: string | null;
  /** Changed keys only. Null on a create or a delete, where one side is absent. */
  diff: Record<string, { from: unknown; to: unknown }> | null;
}

export interface AuditEntryDetail extends AuditEntry {
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
}

export interface Probe {
  ok: boolean; errors: string[]; header_issues: string[];
  total_data_rows: number;
  looks_like_rejects_export: boolean;
  suggested_mode: "replace" | "merge";
  /** The date the sheet NAME suggests. Shown for the operator to confirm; the
   *  server never applies it on its own. */
  suggested_date: string | null;
  sheets: { name: string; business_date: string | null; included: boolean;
    reason: string; data_rows: number }[];
}
export interface UploadResult {
  batch_ref: string; status: string; total_rows: number; accepted_rows: number;
  warned_rows: number; rejected_rows: number; structural_rows: number;
  business_dates: string[]; run_ref: string | null;
  exceptions_by_rule: Record<string, number>;
  message?: string | null;
}
export interface Batch {
  id: number; batch_ref: string; business_date: string | null; file_name: string;
  status: string; total_rows: number; accepted_rows: number; warned_rows: number;
  rejected_rows: number; is_current: boolean;
  supersedes_batch_id: number | null; superseded_by_batch_id: number | null;
  uploaded_at: string; committed_at: string | null;
}
export interface ExceptionRow {
  source_row_no: number | null; origin: string | null; severity: string;
  rule_code: string; field_name: string | null; raw_value: string | null;
  message: string;
}

export interface Performer {
  key: string | number; label: string; net_ftp_profit: Num; total_balance: Num;
  yield_pct: Num; account_count: number; negative_ftp_count: number;
  share_pct?: Num;
}
export interface DimPerformers {
  count: number;
  /** False when only one member is in scope, so there is nothing to rank. */
  rankable: boolean;
  top_by_profit: Performer & { share_pct: Num };
  bottom_by_profit: Performer;
  top_by_yield: Performer;
  bottom_by_yield: Performer;
  profit_yield_diverge: boolean;
}
export type HeadlinePerformers = Partial<Record<Dim, DimPerformers | null>>;

export interface LeaderEntry {
  rank: number; label: string; net_ftp_profit: Num; total_balance: Num;
  yield_pct: Num; account_count: number; share_of_group_pct: Num;
}
export interface Leaderboard {
  available: boolean; reason?: string; group?: Dim; of?: Dim;
  metric?: string; top: number;
  groups: {
    group_key: string | number; group_label: string;
    group_net_ftp_profit: Num; member_count: number;
    leader: LeaderEntry | null;
    laggard: { label: string; net_ftp_profit: Num; yield_pct: Num } | null;
    entries: LeaderEntry[];
  }[];
}

export interface ProductLeadership {
  available: boolean; reason?: string; area?: Dim;
  area_count: number; product_count: number;
  wins_by_product: { product_code: string; product_name: string; areas_won: number }[];
  areas: {
    area_key: string | number; area_label: string; area_net_ftp_profit: Num;
    winner: { product_code: string; product_name: string; net_ftp_profit: Num;
      yield_pct: Num; total_balance: Num };
    dominance_pct: Num;
    runner_up: { product_code: string; net_ftp_profit: Num } | null;
    margin_over_runner_up: Num | null;
    products_present: number;
    breakdown: { product_code: string; net_ftp_profit: Num;
      yield_pct: Num; share_pct: Num }[];
  }[];
}

export interface DataVersion {
  version: string;
  last_run_id: number | null;
  last_run_ref: string | null;
  last_run_at: string | null;
  last_batch_ref: string | null;
  last_batch_status: string | null;
  last_batch_at: string | null;
  latest_business_date: string | null;
  server_time: string;
}

export interface Nii {
  net_interest_income: Num; interest_received: Num; interest_paid: Num;
  business_units_total: Num; lending_spread: Num; deposit_spread: Num;
  treasury_retained: Num; treasury_funding_of_gap: Num;
  liquidity_premium: Num; other_cost: Num;
  business_units_share_pct: Num | null; nii_is_negative: boolean; check: Num;
}
export interface Ratios {
  yield_on_advances_pct: Num | null; cost_of_deposits_pct: Num | null;
  gross_spread_pct: Num | null; nim_pct: Num | null; ftp_yield_pct: Num | null;
  credit_deposit_ratio_pct: Num | null; casa_ratio_pct: Num | null;
  casa_balance: Num; term_balance: Num; advances: Num; deposits: Num;
  funding_gap: Num;
}
export interface DepositCost {
  days: number;
  products: { product_code: string; short_name: string;
    liability_nature: "DEMAND" | "TIME" | null; avg_accounts: Num; avg_balance: Num;
    share_pct: Num | null; interest_paid: Num; cost_pct: Num | null;
    ftp_rate_pct: Num | null; ftp_profit: Num }[];
  total: { avg_balance: Num; interest_paid: Num; cost_pct: Num | null;
    ftp_rate_pct: Num | null; ftp_profit: Num };
}
export interface AdvanceYield {
  days: number;
  products: { product_code: string; short_name: string; avg_accounts: Num;
    avg_balance: Num; share_pct: Num | null; interest_received: Num;
    yield_pct: Num | null; ftp_rate_pct: Num | null; ftp_profit: Num }[];
  total: { avg_balance: Num; interest_received: Num; yield_pct: Num | null;
    ftp_rate_pct: Num | null; ftp_profit: Num };
}
export interface Repricing {
  accounts_below_median: number; balance_below_median: Num; opportunity: Num;
  current_ftp_profit: Num; uplift_pct: Num | null;
  by_product: { product_code: string; accounts: number; opportunity: Num }[];
  top_accounts: { business_date: string; branch_code: string; account_no: string;
    product_code: string; balance: Num; ftp_rate: Num; median_rate: Num; uplift: Num }[];
}
export interface WatchItem {
  severity: "critical" | "serious" | "warning" | "info";
  code: string; title: string; detail: string; amount: Num | null;
}
export interface Watchlist {
  as_of: string | null; period: { start: string; end: string } | null;
  count: number; critical_count: number; items: WatchItem[];
}
export interface PeriodSummary {
  available: boolean; as_of?: string;
  periods: { label: string; start: string; end: string; net_ftp_profit: Num;
    asset_ftp_profit: Num; liability_ftp_profit: Num; days: number;
    avg_daily: Num; yield_pct: Num }[];
}

export interface DeletionImpact {
  batch_ref: string; deletable: boolean; blocked_by: string | null;
  business_dates: string[]; bank_rows: number; fact_rows: number;
  exception_rows: number; staging_rows: number;
  ftp_profit_removed: Num; rows_restored: number;
  batches_restored: string[]; dates_left_empty: string[];
}
export interface DeleteResult {
  batch_ref: string; deleted: boolean;
  fact_rows_removed: number; bank_rows_removed: number;
  ftp_profit_removed: Num; rows_restored: number;
  batches_restored: string[]; dates_recalculated: string[];
  dates_emptied: string[];
}

import { useEffect, useMemo, useRef, useState } from "react";
import {
  api, GlobalConfig, GlobalConfigUpdate, Product, ProductRates,
  ProductRateVersion, StaleDate,
} from "../api";
import { Button, Card, Grid, MiniButton, Pill, Table } from "../components/ui";
import { longDate, rate, shortDate } from "../format";
import { useApp, useAsync } from "../state";

const BASES = ["ACT_365", "ACT_360"] as const;

// Fields take the global form look; only the width is set here.
const field: React.CSSProperties = { width: "100%" };
const label: React.CSSProperties = {
  fontSize: "var(--fs-xs)", fontWeight: 600, letterSpacing: ".05em",
  textTransform: "uppercase", color: "var(--text-muted)",
  marginBottom: 4, display: "block",
};

const hint: React.CSSProperties = {
  fontSize: "var(--fs-xs)", color: "var(--text-muted)", margin: "4px 0 0", lineHeight: 1.4,
};

const today = () => new Date().toISOString().slice(0, 10);

/** A proposed product benchmark handed over by another page (the optional AI
 *  module's "Prepare rate change"). It only pre-fills the form: nothing is
 *  saved until a person with CONFIG_RATE_EDIT reviews it and presses Save. */
interface RateDraft { product_code: string; benchmark_rate: string; note: string }

const DRAFT_KEY = "ftp_rate_draft";

/** Read without removing: React may run a state initialiser twice. */
function readDraft(): RateDraft | null {
  try {
    const raw = sessionStorage.getItem(DRAFT_KEY);
    const d = raw ? JSON.parse(raw) : null;
    return d && typeof d.product_code === "string" && typeof d.benchmark_rate === "string"
      ? { product_code: d.product_code, benchmark_rate: d.benchmark_rate,
          note: typeof d.note === "string" ? d.note.slice(0, 500) : "" }
      : null;
  } catch { return null; }
}

/** Where a component came from, as the label the resolver uses. */
function Source({ source }: { source: string }) {
  return source === "PRODUCT_OVERRIDE"
    ? <Pill tone="info">override</Pill>
    : <Pill tone="neutral">global</Pill>;
}

export default function Rates() {
  const { can, me } = useApp();
  const [refresh, setRefresh] = useState(0);
  const [editing, setEditing] = useState<"version" | "correct" | null>(null);
  const [editingProduct, setEditingProduct] = useState<Product | null>(null);
  const [message, setMessage] =
    useState<{ tone: "good" | "critical"; text: string } | null>(null);

  const [historyProduct, setHistoryProduct] = useState("");
  const [draft, setDraft] = useState<RateDraft | null>(readDraft);
  // Taken once: a reload of this page must not reopen an old proposal.
  useEffect(() => { try { sessionStorage.removeItem(DRAFT_KEY); } catch { /* blocked */ } }, []);
  const pageRef = useRef<HTMLDivElement | null>(null);
  const globalFormRef = useRef<HTMLDivElement | null>(null);
  const productFormRef = useRef<HTMLDivElement | null>(null);

  // The forms open below their tables: pull the viewport down to the one just
  // opened so it is on screen instead of silently appearing off-screen.
  useEffect(() => {
    const target = editingProduct ? productFormRef.current
      : editing ? globalFormRef.current : null;
    target?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [editing, editingProduct]);

  const current = useAsync(() => api.globalConfig(), [refresh]);
  const history = useAsync(() => api.globalConfigHistory(50), [refresh]);
  const productHistory = useAsync(() => api.productRateHistory(), [refresh]);
  const stale = useAsync(() => api.staleDates(), [refresh]);

  // Five products, so five requests is cheaper than a new endpoint. If the
  // product master grows past a screenful this wants a batch route.
  const productRates = useAsync(async () => {
    const list = await api.products(false);
    const settled = await Promise.all(list.map(async (p) => {
      try { return { p, r: await api.productRates(p.product_code) }; }
      // A product with no resolvable benchmark 404s here. That is the state
      // worth showing, not hiding: it will fail at the next validation.
      catch { return { p, r: null }; }
    }));
    return settled;
  }, [refresh]);

  // Editing the global layer reprices every branch, so HO scope is required in
  // addition to the permission. The API enforces the same rule.
  const editable = can("CONFIG_RATE_EDIT") && me?.scope_level === "HO";
  const reload = () => { setRefresh((r) => r + 1); setEditing(null); setEditingProduct(null); };
  // After a save the outcome -- and, for a backdated change, the recalculate
  // or keep decision -- is at the top of the page, so bring the reader there.
  const afterSave = (text: string) => {
    setMessage({ tone: "good", text });
    reload();
    pageRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const cfg = current.data;

  // Open the drafted product's form once, when the products have loaded; a
  // later refetch must not pull the reader back to it.
  const draftOpened = useRef(false);
  useEffect(() => {
    if (!draft || !productRates.data || draftOpened.current) return;
    draftOpened.current = true;
    const hit = productRates.data.find((x) => x.p.product_code === draft.product_code);
    if (hit && can("CONFIG_RATE_EDIT")) { setEditingProduct(hit.p); setEditing(null); }
    else setDraft(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft, productRates.data]);

  const productHistoryRows = (productHistory.data ?? []).filter(
    (h) => !historyProduct || h.product_code === historyProduct);
  const historyProducts = [...new Set(
    (productHistory.data ?? []).map((h) => h.product_code))];

  // A backdated rate change does not reprice days already uploaded. Those days
  // are listed here until someone chooses to restate them.
  const staleDates: StaleDate[] = stale.data ?? [];
  // Awaiting a decision, versus deliberately kept on the earlier rates.
  const pending = staleDates.filter((d) => !d.kept);
  const kept = staleDates.filter((d) => d.kept);
  const [busy, setBusy] = useState<"recalc" | "keep" | null>(null);
  const [showKept, setShowKept] = useState(false);

  const daysLabel = (n: number) => `${n} uploaded ${n === 1 ? "day" : "days"}`;

  async function recalculate(list: StaleDate[]) {
    const dates = list.filter((d) => !d.blocked_by).map((d) => d.business_date);
    if (!dates.length) return;
    if (!window.confirm(
      `Recalculate ${daysLabel(dates.length)} on the rates now in force?\n\n`
      + `${dates.map(longDate).join(", ")}\n\n`
      + "The current figures for these days are replaced. The previous "
      + "figures are kept as superseded and the run is recorded in the audit log.",
    )) return;
    setBusy("recalc");
    setMessage(null);
    try {
      const out = await api.recalculateDates(dates, "Backdated rate change");
      setMessage({ tone: "good", text:
        `${out.dates_recalculated.map(shortDate).join(", ")} recalculated `
        + `(${out.rows.toLocaleString("en-IN")} rows, run ${out.run_ref}).` });
      reload();
    } catch (e) {
      setMessage({ tone: "critical", text: (e as Error).message });
    } finally { setBusy(null); }
  }

  async function keepAsIs(list: StaleDate[]) {
    const dates = list.map((d) => d.business_date);
    if (!window.confirm(
      `Keep the current figures for ${daysLabel(dates.length)}?\n\n`
      + `${dates.map(longDate).join(", ")}\n\n`
      + "These days stay on the earlier rates. The new rates apply to every "
      + "upload from now on, including a re-upload of one of these days. "
      + "You can still recalculate them later from this page.",
    )) return;
    setBusy("keep");
    setMessage(null);
    try {
      const out = await api.keepDates(dates, "Backdated rate change");
      setMessage({ tone: "good", text:
        `${out.dates_kept.map(shortDate).join(", ")} kept on the earlier rates. `
        + "New uploads use the new rates." });
      reload();
    } catch (e) {
      setMessage({ tone: "critical", text: (e as Error).message });
    } finally { setBusy(null); }
  }

  // A product benchmark always overrides the global one, so these are the
  // products that depend on the global benchmark being set. An unresolved row
  // (no rate at all) counts the same as one inheriting the global default.
  const dependsOnGlobalBenchmark = (productRates.data ?? [])
    .filter(({ r }) => r === null || r.benchmark_source === "GLOBAL_DEFAULT")
    .map(({ p }) => p.product_code);

  return (
    <div ref={pageRef} style={{ display: "flex", flexDirection: "column", gap: 14,
                                scrollMarginTop: 24 }}>
      {message && (
        <div style={{
          padding: "9px 12px", borderRadius: 8, fontSize: "var(--fs-base)",
          border: `1px solid ${message.tone === "good"
            ? "var(--status-good)" : "var(--status-critical)"}`,
          background: "var(--surface-2)",
        }}>
          <Pill tone={message.tone}>{message.tone === "good" ? "Done" : "Refused"}</Pill>
          <span style={{ marginLeft: 8, color: "var(--text-secondary)" }}>{message.text}</span>
        </div>
      )}

      {pending.length > 0 && (
        <div style={{
          padding: "10px 12px", borderRadius: 8, fontSize: "var(--fs-base)",
          border: "1px solid var(--status-warning)", background: "var(--surface-2)",
          display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap",
        }}>
          <Pill tone="warning">Decision needed</Pill>
          <span style={{ flex: "1 1 320px", color: "var(--text-secondary)",
                         lineHeight: 1.5 }}>
            <b style={{ color: "var(--text-primary)" }}>
              Rates changed for days already uploaded:{" "}
              {pending.map((d) => shortDate(d.business_date)).join(", ")}
            </b>
            {" "}({[...new Set(pending.flatMap((d) => d.products))].join(", ")};{" "}
            {pending.reduce((a, d) => a + d.rows, 0).toLocaleString("en-IN")} rows).
            {" "}These days still show figures from the old rates. Recalculate them,
            or keep them as they are. New uploads use the new rates either way.
            {pending.some((d) => d.blocked_by) && (
              <span style={{ color: "var(--status-critical)" }}>
                {" "}Rates cannot be resolved for{" "}
                {pending.filter((d) => d.blocked_by)
                  .map((d) => shortDate(d.business_date)).join(", ")}.
              </span>
            )}
          </span>
          {can("CONFIG_RATE_EDIT") && (
            <span style={{ display: "inline-flex", gap: 8 }}>
              {pending.some((d) => !d.blocked_by) && (
                <Button variant="primary" onClick={() => recalculate(pending)}
                        disabled={busy !== null}>
                  {busy === "recalc" ? "Recalculating…" : "Recalculate now"}
                </Button>
              )}
              <Button onClick={() => keepAsIs(pending)} disabled={busy !== null}>
                {busy === "keep" ? "Saving…" : "Keep as it is"}
              </Button>
            </span>
          )}
        </div>
      )}

      {kept.length > 0 && (
        <div style={{ fontSize: "var(--fs-sm)", color: "var(--text-muted)", margin: "0 4px",
                      lineHeight: 1.6 }}>
          {daysLabel(kept.length)} kept on earlier rates
          {" "}({kept.reduce((a, d) => a + d.rows, 0).toLocaleString("en-IN")} rows).{" "}
          <MiniButton onClick={() => setShowKept((v) => !v)}>
            {showKept ? "Hide" : "Show"}
          </MiniButton>
          {showKept && (
            <div style={{ marginTop: 6, display: "flex", alignItems: "center",
                          gap: 10, flexWrap: "wrap" }}>
              <span>
                {kept.map((d) => `${shortDate(d.business_date)} (${d.products.join(", ")})`)
                  .join(" · ")}
                {kept[0].kept_by && ` — kept by ${kept[0].kept_by}`}
              </span>
              {can("CONFIG_RATE_EDIT") && (
                <MiniButton onClick={() => recalculate(kept)} disabled={busy !== null}>
                  Recalculate these instead
                </MiniButton>
              )}
            </div>
          )}
        </div>
      )}

      <Card
        title="Global rate defaults"
        subtitle={cfg
          ? `Version ${cfg.version}, effective from ${longDate(cfg.effective_from)}`
          : current.error ?? "Loading…"}
        actions={editable && cfg && (
          <>
            {cfg.editable_in_place && (
              <MiniButton onClick={() => { setEditing("correct"); setMessage(null); }}>
                Correct this version
              </MiniButton>
            )}
            <Button variant="primary"
                    onClick={() => { setEditing("version"); setMessage(null); }}>
              Change from a date
            </Button>
          </>
        )}>
        {current.error && !cfg && (
          <p style={{ fontSize: "var(--fs-base)", color: "var(--status-critical)", margin: "6px 4px" }}>
            {current.error}
          </p>
        )}
        {cfg && (
          <>
            <Grid cols="repeat(auto-fit, minmax(150px, 1fr))" gap={12}>
              <Field name="Benchmark rate"
                     value={cfg.benchmark_rate === null
                       ? "not set globally" : `${rate(cfg.benchmark_rate, 4)} %`}
                     hint={cfg.benchmark_rate === null
                       ? "Each product defines its own"
                       : "Applies where no override exists"} />
              <Field name="Liquidity cost" value={`${rate(cfg.liquidity_cost, 4)} %`}
                     hint="Deducted from every spread" />
              <Field name="Other cost" value={`${rate(cfg.other_cost, 4)} %`}
                     hint="Deducted from every spread" />
              <Field name="Day-count basis"
                     value={cfg.day_count_basis.replace("_", "/")}
                     hint={cfg.day_count_basis === "ACT_365"
                       ? "Divisor 36,500" : "Divisor 36,000"} />
            </Grid>
          </>
        )}
      </Card>

      {editing && cfg && editable && (
        <div ref={globalFormRef}
             style={{ scrollMarginTop: 24, scrollSnapMarginTop: 24 }}>
        <GlobalForm
          mode={editing}
          cfg={cfg}
          versions={(history.data ?? []).filter((h) => h.status === "APPROVED")}
          dependsOnGlobalBenchmark={dependsOnGlobalBenchmark}
          onCancel={() => setEditing(null)}
          onSaved={afterSave}
          onError={(text) => setMessage({ tone: "critical", text })}
        />
        </div>
      )}

      <Card title="Effective rates by product"
            subtitle={`${productRates.data?.length ?? 0} active products, as at today`}
            footnote="Rates currently applied. Each component is tagged with its source: the product's own override, or the global default.">
        <Table
          rows={productRates.data ?? []}
          csvName="effective-rates.csv"
          searchPlaceholder="Search product code or short name…"
          search={({ p }) => [p.product_code, p.short_name, p.side].join(" ")}
          empty={productRates.error ?? "No active products."}
          cols={[
            { key: "code", label: "Product",
              render: ({ p }) => <b>{p.product_code}</b>,
              value: ({ p }) => p.product_code },
            { key: "name", label: "Short name", render: ({ p }) => p.short_name,
              value: ({ p }) => p.short_name },
            { key: "side", label: "Side",
              render: ({ p }) => <Pill tone={p.side === "ASSET" ? "info" : "neutral"}>
                {p.side}</Pill>, value: ({ p }) => p.side },
            { key: "bm", label: "Benchmark", align: "right",
              render: ({ r }) => r
                ? <span>{rate(r.benchmark_rate, 4)} <Source source={r.benchmark_source} /></span>
                : <Pill tone="critical">unresolved</Pill>,
              value: ({ r }) => r?.benchmark_rate ?? "" },
            { key: "lq", label: "Liquidity", align: "right",
              render: ({ r }) => r
                ? <span>{rate(r.liquidity_cost, 4)} <Source source={r.liquidity_source} /></span>
                : "—",
              value: ({ r }) => r?.liquidity_cost ?? "" },
            { key: "oc", label: "Other", align: "right",
              render: ({ r }) => r
                ? <span>{rate(r.other_cost, 4)} <Source source={r.other_source} /></span>
                : "—",
              value: ({ r }) => r?.other_cost ?? "" },
            {
              key: "act", label: "", align: "right" as const,
              render: ({ p }: { p: Product; r: ProductRates | null }) => (
                <span style={{ display: "inline-flex", gap: 6 }}>
                  <MiniButton onClick={() => {
                    setHistoryProduct(p.product_code);
                    document.getElementById("product-rate-history")
                      ?.scrollIntoView({ behavior: "smooth", block: "start" });
                  }}>History</MiniButton>
                  {can("CONFIG_RATE_EDIT") && (
                    <MiniButton onClick={() => {
                      setEditingProduct(p); setEditing(null); setMessage(null);
                    }}>Set rates</MiniButton>
                  )}
                </span>
              ),
            },
          ]}
        />
      </Card>

      {editingProduct && can("CONFIG_RATE_EDIT") && (
        <div ref={productFormRef}
             style={{ scrollMarginTop: 24, scrollSnapMarginTop: 24 }}>
        <ProductRateForm
          product={editingProduct}
          rates={productRates.data?.find(
            (x) => x.p.product_code === editingProduct.product_code)?.r ?? null}
          versions={(productHistory.data ?? []).filter((h) =>
            h.product_code === editingProduct.product_code && h.status === "APPROVED")}
          draft={draft?.product_code === editingProduct.product_code ? draft : undefined}
          onCancel={() => { setEditingProduct(null); setDraft(null); }}
          onSaved={(text) => { setDraft(null); afterSave(text); }}
          onError={(text) => setMessage({ tone: "critical", text })}
        />
        </div>
      )}

      <div id="product-rate-history"
           style={{ scrollMarginTop: 24, scrollSnapMarginTop: 24 }}>
      <Card title="Product rate history"
            subtitle={`${productHistoryRows.length} versions`
                      + (historyProduct ? ` for ${historyProduct}` : " across all products")}
            actions={
              <select style={{ ...field, width: "auto" }} value={historyProduct}
                      aria-label="Filter by product"
                      onChange={(e) => setHistoryProduct(e.target.value)}>
                <option value="">All products</option>
                {historyProducts.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            }
            footnote="Every rate change adds a version and closes the previous one; nothing is overwritten. Rows priced counts the current figures each version produced.">
        <Table rows={productHistoryRows} csvName="product-rate-history.csv"
               searchPlaceholder="Search product, short name, user or note…"
               search={(h) => [
                 h.product_code, h.short_name, `v${h.version}`, h.changed_by, h.note,
                 h.status === "SUPERSEDED" ? "superseded"
                   : h.effective_to ? "" : "in force",
               ].join(" ")}
               empty={productHistory.error ?? "No product rate versions."}
               cols={[
                 { key: "p", label: "Product",
                   render: (h: ProductRateVersion) => <b>{h.product_code}</b>,
                   value: (h: ProductRateVersion) => h.product_code },
                 { key: "v", label: "Version", render: (h) => `v${h.version}`,
                   value: (h) => h.version },
                 { key: "from", label: "Effective from",
                   render: (h) => longDate(h.effective_from),
                   value: (h) => h.effective_from },
                 { key: "to", label: "Until",
                   render: (h) => h.status === "SUPERSEDED"
                     ? <Pill tone="neutral">superseded</Pill>
                     : h.effective_to
                       ? longDate(h.effective_to)
                       : <Pill tone="good">in force</Pill>,
                   value: (h) => h.status === "SUPERSEDED"
                     ? "superseded" : h.effective_to ?? "" },
                 { key: "bm", label: "Benchmark", align: "right",
                   render: (h) => h.benchmark_rate === null ? "global" : rate(h.benchmark_rate, 4),
                   value: (h) => h.benchmark_rate ?? "" },
                 { key: "lq", label: "Liquidity", align: "right",
                   render: (h) => h.liquidity_cost === null
                     ? <span style={{ color: "var(--text-muted)" }}>global</span>
                     : rate(h.liquidity_cost, 4),
                   value: (h) => h.liquidity_cost ?? "global" },
                 { key: "oc", label: "Other", align: "right",
                   render: (h) => h.other_cost === null
                     ? <span style={{ color: "var(--text-muted)" }}>global</span>
                     : rate(h.other_cost, 4),
                   value: (h) => h.other_cost ?? "global" },
                 { key: "by", label: "Changed by", render: (h) => h.changed_by ?? "—",
                   value: (h) => h.changed_by ?? "" },
                 { key: "at", label: "Changed on",
                   render: (h) => h.changed_at ? longDate(h.changed_at.slice(0, 10)) : "—",
                   value: (h) => h.changed_at ?? "" },
                 { key: "rows", label: "Rows priced", align: "right",
                   render: (h) => h.rows_priced.toLocaleString("en-IN"),
                   value: (h) => h.rows_priced },
                 { key: "note", label: "Note",
                   render: (h) => <span style={{ color: "var(--text-muted)" }}>{h.note}</span>,
                   value: (h) => h.note ?? "" },
               ]} />
      </Card>
      </div>

      <Card title="Global version history"
            subtitle={`${history.data?.length ?? 0} versions`}
            footnote="Superseded versions are retained rather than overwritten, so past figures remain traceable to the rates that produced them.">
        <Table rows={history.data ?? []} csvName="global-rate-history.csv"
               searchPlaceholder="Search version or note…"
               search={(h) => [
                 `v${h.version}`, h.note, h.day_count_basis.replace("_", "/"),
                 h.status === "SUPERSEDED" ? "superseded"
                   : h.effective_to ? "" : "in force",
               ].join(" ")}
               empty={history.error ?? "No configuration versions."}
               cols={[
                 { key: "v", label: "Version", render: (h) => <b>v{h.version}</b>,
                   value: (h) => h.version },
                 { key: "from", label: "Effective from",
                   render: (h) => longDate(h.effective_from),
                   value: (h) => h.effective_from },
                 { key: "to", label: "Until",
                   render: (h) => h.status === "SUPERSEDED"
                     ? <Pill tone="neutral">superseded</Pill>
                     : h.effective_to
                       ? longDate(h.effective_to)
                       : <Pill tone="good">in force</Pill>,
                   value: (h) => h.status === "SUPERSEDED"
                     ? "superseded" : h.effective_to ?? "" },
                 { key: "bm", label: "Benchmark", align: "right",
                   render: (h) => h.benchmark_rate === null ? "—" : rate(h.benchmark_rate, 4),
                   value: (h) => h.benchmark_rate ?? "" },
                 { key: "lq", label: "Liquidity", align: "right",
                   render: (h) => rate(h.liquidity_cost, 4),
                   value: (h) => h.liquidity_cost ?? "" },
                 { key: "oc", label: "Other", align: "right",
                   render: (h) => rate(h.other_cost, 4),
                   value: (h) => h.other_cost ?? "" },
                 { key: "dc", label: "Basis",
                   render: (h) => h.day_count_basis.replace("_", "/"),
                   value: (h) => h.day_count_basis },
                 { key: "rows", label: "Rows priced", align: "right",
                   render: (h) => h.rows_priced.toLocaleString("en-IN"),
                   value: (h) => h.rows_priced },
                 { key: "note", label: "Note",
                   render: (h) => <span style={{ color: "var(--text-muted)" }}>{h.note}</span>,
                   value: (h) => h.note ?? "" },
               ]} />
      </Card>

      {!editable && can("CONFIG_RATE_VIEW") && (
        <p style={{ fontSize: "var(--fs-sm)", color: "var(--text-muted)", margin: "0 4px",
                    lineHeight: 1.5 }}>
          {can("CONFIG_RATE_EDIT")
            ? "Global defaults apply to every branch and can only be changed at "
              + `head office. Your scope is ${me?.scope_label ?? "below head office"}.`
            : "You have view-only access to the rate configuration."}
        </p>
      )}
    </div>
  );
}

function Field({ name, value, hint }: { name: string; value: string; hint?: string }) {
  return (
    <div style={{
      padding: "10px 12px", borderRadius: 8, background: "var(--surface-2)",
      border: "1px solid var(--border)",
    }}>
      <div style={label}>{name}</div>
      <div className="tnum" style={{ fontSize: 17, fontWeight: 600,
                                     color: "var(--text-primary)" }}>{value}</div>
      {hint && <div style={{ fontSize: "var(--fs-xs)", color: "var(--text-muted)", marginTop: 3 }}>
        {hint}</div>}
    </div>
  );
}

function GlobalForm({
  mode, cfg, versions, dependsOnGlobalBenchmark, onCancel, onSaved, onError,
}: {
  mode: "version" | "correct";
  cfg: GlobalConfig;
  /** Approved versions, to warn which a backdated date would supersede. */
  versions: GlobalConfig[];
  /** Products with no benchmark of their own, which a NULL global would leave
   *  unpriceable. Checked here so the refusal is not a round trip. */
  dependsOnGlobalBenchmark: string[];
  onCancel: () => void;
  onSaved: (msg: string) => void;
  onError: (msg: string) => void;
}) {
  const [noBenchmark, setNoBenchmark] = useState(cfg.benchmark_rate === null);
  const [bench, setBench] = useState(
    cfg.benchmark_rate === null ? "" : String(cfg.benchmark_rate));
  const [liquidity, setLiquidity] = useState(String(cfg.liquidity_cost ?? ""));
  const [other, setOther] = useState(String(cfg.other_cost ?? ""));
  const [basis, setBasis] = useState(cfg.day_count_basis);
  const [from, setFrom] = useState(today());
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);

  const correcting = mode === "correct";
  const wouldBreak = noBenchmark ? dependsOnGlobalBenchmark : [];

  async function save() {
    setSaving(true);
    const body: GlobalConfigUpdate = {
      benchmark_rate: noBenchmark ? null : bench,
      liquidity_cost: liquidity,
      other_cost: other,
      day_count_basis: basis,
      note: note || null,
    };
    try {
      if (correcting) {
        const out = await api.correctGlobalConfig(body);
        onSaved(`Version ${out.version} updated.`);
      } else {
        const out = await api.setGlobalConfig({ ...body, effective_from: from });
        onSaved(`Version ${out.version} effective from `
                + `${longDate(out.effective_from)}. Version ${cfg.version} closed.`);
      }
    } catch (e) { onError((e as Error).message); }
    finally { setSaving(false); }
  }

  return (
    <Card
      title={correcting
        ? `Correct global version ${cfg.version}`
        : "Change the global defaults from a date"}
      subtitle={correcting
        ? "Rewrites this version. Use for a value that was incorrect from the "
          + "start, where a new version would imply a rate change that did not "
          + "occur."
        : "Closes the current version and opens a new one from the selected "
          + "date. Existing figures are unaffected."}>
      <Grid cols="repeat(auto-fit, minmax(170px, 1fr))" gap={12}>
        {!correcting && (
          <div>
            <label style={label} htmlFor="g-from">Effective from</label>
            <input id="g-from" type="date" style={field} value={from}
                   onChange={(e) => setFrom(e.target.value)} />
          </div>
        )}
        <div>
          <label style={label} htmlFor="g-liq">Liquidity cost %</label>
          <input id="g-liq" style={field} value={liquidity} inputMode="decimal"
                 onChange={(e) => setLiquidity(e.target.value)} placeholder="0.30" />
        </div>
        <div>
          <label style={label} htmlFor="g-oth">Other cost %</label>
          <input id="g-oth" style={field} value={other} inputMode="decimal"
                 onChange={(e) => setOther(e.target.value)} placeholder="0.05" />
        </div>
        <div>
          <label style={label} htmlFor="g-basis">Day-count basis</label>
          <select id="g-basis" style={field} value={basis}
                  onChange={(e) => setBasis(e.target.value as typeof basis)}>
            {BASES.map((b) => (
              <option key={b} value={b}>{b.replace("_", "/")}</option>
            ))}
          </select>
        </div>
        <div>
          <label style={label} htmlFor="g-bench">Benchmark rate %</label>
          <input id="g-bench" style={{ ...field, opacity: noBenchmark ? .5 : 1 }}
                 value={noBenchmark ? "" : bench} disabled={noBenchmark}
                 inputMode="decimal"
                 onChange={(e) => setBench(e.target.value)} placeholder="7.25" />
          <label style={{ display: "flex", alignItems: "center", gap: 6,
                          marginTop: 6, fontSize: "var(--fs-sm)",
                          color: "var(--text-secondary)" }}>
            <input type="checkbox" checked={noBenchmark}
                   onChange={(e) => setNoBenchmark(e.target.checked)} />
            No global benchmark
          </label>
        </div>
        <div style={{ gridColumn: "1 / -1" }}>
          <label style={label} htmlFor="g-note">Reason for the change</label>
          <input id="g-note" style={field} value={note}
                 onChange={(e) => setNote(e.target.value)}
                 placeholder="Recorded in the activity log" />
        </div>
      </Grid>

      {noBenchmark && (
        <div style={{ marginTop: 12, padding: "10px 12px", borderRadius: 8,
                      background: "var(--surface-2)",
                      border: `1px solid var(--status-${
                        wouldBreak.length ? "critical" : "warning"})` }}>
          <Pill tone={wouldBreak.length ? "critical" : "warning"}>
            {wouldBreak.length ? "Not allowed" : "Every product needs its own"}
          </Pill>
          <span style={{ marginLeft: 8, fontSize: "var(--fs-base)", color: "var(--text-secondary)" }}>
            {wouldBreak.length
              ? `${wouldBreak.join(", ")} `
                + `${wouldBreak.length === 1 ? "has" : "have"} no benchmark of `
                + `${wouldBreak.length === 1 ? "its" : "their"} own and would not `
                + "be priceable. Set a product benchmark first."
              : "Every product currently defines its own benchmark, so clearing "
                + "the global one is safe."}
          </span>
        </div>
      )}

      {!correcting && (
        <BackdateNotice from={from} versions={versions.map((v) => ({
          version: v.version, effective_from: v.effective_from,
          label: `liquidity ${rate(v.liquidity_cost, 2)} %, other ${rate(v.other_cost, 2)} %`,
        }))} />
      )}
      <div style={{ display: "flex", gap: 8, marginTop: 14, alignItems: "center" }}>
        <Button variant="primary" onClick={save}
                disabled={saving || !from || !liquidity || !other
                          || wouldBreak.length > 0
                          || (!noBenchmark && !bench)}>
          {saving ? "Saving…" : correcting
            ? `Correct v${cfg.version}` : "Save new version"}
        </Button>
        <Button onClick={onCancel}>Cancel</Button>
      </div>
    </Card>
  );
}

function ProductRateForm({ product, rates, versions, draft, onCancel, onSaved, onError }: {
  product: Product;
  rates: ProductRates | null;
  /** A proposed benchmark to start from; see `RateDraft`. */
  draft?: RateDraft;
  /** The product's approved versions, to warn which a backdated date replaces. */
  versions: ProductRateVersion[];
  onCancel: () => void;
  onSaved: (msg: string) => void;
  onError: (msg: string) => void;
}) {
  // What the product does today, only to say so when the form would change it.
  const ownToday = useMemo(() => ({
    liquidity: rates?.liquidity_source === "PRODUCT_OVERRIDE",
    other: rates?.other_source === "PRODUCT_OVERRIDE",
  }), [rates]);

  const [bench, setBench] = useState(draft?.benchmark_rate
    ?? (rates?.benchmark_rate == null ? "" : String(rates.benchmark_rate)));
  // Inheriting the global default is the norm, so a new version inherits
  // unless the user deliberately unticks the box and enters a product value.
  // A drafted benchmark change is about the benchmark only, so it keeps the
  // product's own liquidity and other costs as they are.
  const [liqOverride, setLiqOverride] = useState(Boolean(draft) && ownToday.liquidity);
  const [othOverride, setOthOverride] = useState(Boolean(draft) && ownToday.other);
  const [liquidity, setLiquidity] = useState(String(rates?.liquidity_cost ?? ""));
  const [other, setOther] = useState(String(rates?.other_cost ?? ""));
  const [from, setFrom] = useState(today());
  const [note, setNote] = useState(draft?.note ?? "");
  const [saving, setSaving] = useState(false);

  async function save() {
    setSaving(true);
    try {
      const out = await api.setProductRate(product.product_code, {
        benchmark_rate: bench,
        // null means inherit the global default; "0" would be an explicit zero.
        liquidity_cost: liqOverride ? liquidity : null,
        other_cost: othOverride ? other : null,
        effective_from: from,
        note: note.trim() || null,
      });
      onSaved(`${product.product_code} rates version ${out.version} effective `
              + `from ${longDate(out.effective_from)}.`);
    } catch (e) { onError((e as Error).message); }
    finally { setSaving(false); }
  }

  return (
    <Card title={`Set rates for ${product.product_code}`}
          subtitle={`${product.short_name} · new effective-dated version`}>
      {draft && (
        <p style={{ margin: "0 0 12px", fontSize: "var(--fs-base)", color: "var(--text-secondary)",
                    lineHeight: 1.5 }}>
          <Pill tone="info">Proposed</Pill>{" "}
          Benchmark {rate(rates?.benchmark_rate, 2)} % → {draft.benchmark_rate} %, prepared from market
          data. Check the rate, the date and the note, then save; nothing changes until you do.
        </p>
      )}
      <Grid cols="repeat(auto-fit, minmax(170px, 1fr))" gap={12}>
        <div>
          <label style={label} htmlFor="pr-from">Effective from</label>
          <input id="pr-from" type="date" style={field} value={from}
                 onChange={(e) => setFrom(e.target.value)} />
        </div>
        <div>
          <label style={label} htmlFor="pr-bench">Benchmark rate %</label>
          <input id="pr-bench" style={field} value={bench} inputMode="decimal"
                 onChange={(e) => setBench(e.target.value)} placeholder="7.25" />
        </div>
        <div>
          <label style={label} htmlFor="pr-liq">Liquidity cost %</label>
          <input id="pr-liq" style={{ ...field, opacity: liqOverride ? 1 : .5 }}
                 value={liqOverride ? liquidity : ""} disabled={!liqOverride}
                 inputMode="decimal"
                 onChange={(e) => setLiquidity(e.target.value)} />
          <label style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 6,
                          fontSize: "var(--fs-sm)", color: "var(--text-secondary)" }}>
            <input type="checkbox" checked={!liqOverride}
                   onChange={(e) => setLiqOverride(!e.target.checked)} />
            Inherit the global default
          </label>
          {!liqOverride && ownToday.liquidity && (
            <p style={hint}>
              Replaces this product's own {rate(rates?.liquidity_cost, 4)} %.
            </p>
          )}
        </div>
        <div>
          <label style={label} htmlFor="pr-oth">Other cost %</label>
          <input id="pr-oth" style={{ ...field, opacity: othOverride ? 1 : .5 }}
                 value={othOverride ? other : ""} disabled={!othOverride}
                 inputMode="decimal"
                 onChange={(e) => setOther(e.target.value)} />
          <label style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 6,
                          fontSize: "var(--fs-sm)", color: "var(--text-secondary)" }}>
            <input type="checkbox" checked={!othOverride}
                   onChange={(e) => setOthOverride(!e.target.checked)} />
            Inherit the global default
          </label>
          {!othOverride && ownToday.other && (
            <p style={hint}>
              Replaces this product's own {rate(rates?.other_cost, 4)} %.
            </p>
          )}
        </div>
      </Grid>
      <div style={{ marginTop: 12 }}>
        <label style={label} htmlFor="pr-note">Reason / note</label>
        <input id="pr-note" style={field} value={note} maxLength={500}
               onChange={(e) => setNote(e.target.value)}
               placeholder="e.g. Board-approved benchmark revision" />
      </div>
      <BackdateNotice from={from} versions={versions.map((v) => ({
        version: v.version, effective_from: v.effective_from,
        label: `benchmark ${rate(v.benchmark_rate, 2)} %`,
      }))} />
      <div style={{ display: "flex", gap: 8, marginTop: 14 }}>
        <Button variant="primary" onClick={save}
                disabled={saving || !from || !bench || (liqOverride && !liquidity)
                          || (othOverride && !other)}>
          {saving ? "Saving…" : "Save new version"}
        </Button>
        <Button onClick={onCancel}>Cancel</Button>
      </div>
    </Card>
  );
}

/** What saving a version from `from` will do to the ones already in place.
 *
 * A date on or before an existing version's start supersedes it: kept in the
 * history, no longer in force. A past date leaves days already uploaded on the
 * old rates until someone recalculates them from the banner on this page. */
function BackdateNotice({ from, versions }: {
  from: string;
  versions: { version: number; effective_from: string; label: string }[];
}) {
  if (!from) return null;
  const replaced = versions.filter((v) => v.effective_from >= from)
    .sort((a, b) => a.effective_from.localeCompare(b.effective_from));
  const past = from < today();
  if (!replaced.length && !past) return null;
  return (
    <div style={{
      marginTop: 12, padding: "9px 11px", borderRadius: 8, fontSize: "var(--fs-base)",
      border: "1px solid var(--status-warning)", background: "var(--surface-2)",
      color: "var(--text-secondary)", lineHeight: 1.55,
    }}>
      <Pill tone="warning">Backdated change</Pill>
      {replaced.length > 0 && (
        <div style={{ marginTop: 6 }}>
          This replaces{" "}
          {replaced.map((v, i) => (
            <span key={v.version}>
              {i > 0 && (i === replaced.length - 1 ? " and " : ", ")}
              <b>v{v.version}</b> ({v.label} from {longDate(v.effective_from)})
            </span>
          ))}
          . {replaced.length === 1 ? "It stays" : "They stay"} in the history marked
          superseded.
        </div>
      )}
      {past && (
        <div style={{ marginTop: 6 }}>
          Days already uploaded from {longDate(from)} keep their current figures
          for now. After saving they are listed at the top of this page, where you
          choose <b>Recalculate now</b> or <b>Keep as it is</b>. New uploads use the
          new rate either way.
        </div>
      )}
    </div>
  );
}

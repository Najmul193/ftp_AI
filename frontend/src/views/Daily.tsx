import { useMemo, useState } from "react";
import { api, type WatchItem } from "../api";
import Chart, { useTokens } from "../components/Chart";
import { waterfallOption } from "../components/waterfall";
import { Card, Empty, Grid, MiniButton, Pill, Stat, Table } from "../components/ui";
import { compact, longDate, money, n, pct } from "../format";
import { useApp, useAsync } from "../state";
import { useWhy } from "../ai/why";

/** The screen a bank runs on in the morning: what needs attention, where the
 *  margin went, and whether the ratios moved. */
export default function Daily() {
  // "Why?" buttons exist only while the optional AI module is on.
  const why = useWhy();
  const { filters, setFilters, me } = useApp();
  const t = useTokens();

  const watch = useAsync(() => api.watchlist(filters), [filters]);
  const nii = useAsync(() => api.nii(filters), [filters]);
  const ratios = useAsync(() => api.ratios(filters), [filters]);
  const periods = useAsync(() => api.periodSummary(filters), [filters]);
  const repricing = useAsync(() => api.repricing(filters), [filters]);
  const depositCost = useAsync(() => api.depositCost(filters), [filters]);
  const advanceYield = useAsync(() => api.advanceYield(filters), [filters]);
  const summary = useAsync(() => api.summary(filters), [filters]);
  const dcSort = useSort<DcKey>("avg_balance");
  const aySort = useSort<AyKey>("avg_balance");

  // -- acknowledged watchlist items ---------------------------------------- #
  // An operator acknowledges an item once and it stops imposing on every
  // reload; acking is personal, so the set is kept per user in localStorage.
  const ackKey = `ftp_acked_${me?.username ?? "local"}`;
  const readAcked = () => {
    try {
      return new Set<string>(JSON.parse(localStorage.getItem(ackKey) ?? "[]") as string[]);
    } catch {
      return new Set<string>();
    }
  };
  const [acked, setAcked] = useState<Set<string>>(readAcked);
  const fingerprint = (i: WatchItem) => `${i.severity}:${i.code}:${i.title}`;
  const writeAcked = (s: Set<string>) => {
    localStorage.setItem(ackKey, JSON.stringify([...s]));
    setAcked(s);
  };
  const ack = (i: WatchItem) => {
    const s = new Set(acked);
    s.add(fingerprint(i));
    writeAcked(s);
  };
  const restore = () => writeAcked(new Set());

  const visibleItems = (watch.data?.items ?? []).filter(
    (i) => !acked.has(fingerprint(i)));
  const ackedCount = (watch.data?.items.length ?? 0) - visibleItems.length;
  const nothingOutstanding = Boolean(watch.data) && visibleItems.length === 0;

  const r = ratios.data;
  const v = nii.data;
  const dc = depositCost.data;
  type DcRow = NonNullable<typeof dc>["products"][number] & { is_total?: boolean };
  const dcRows: DcRow[] = !dc?.products.length ? [] : [
    ...dcSort.apply(dc.products),
    { ...dc.total, product_code: "Total", short_name: "All deposit products",
      liability_nature: null, avg_accounts: String(dc.products.reduce((a, p) => a + n(p.avg_accounts), 0)),
      share_pct: "100", is_total: true },
  ];
  const ay = advanceYield.data;
  type AyRow = NonNullable<typeof ay>["products"][number] & { is_total?: boolean };
  const ayRows: AyRow[] = !ay?.products.length ? [] : [
    ...aySort.apply(ay.products),
    { ...ay.total, product_code: "Total", short_name: "All loan products",
      avg_accounts: String(ay.products.reduce((a, p) => a + n(p.avg_accounts), 0)),
      share_pct: "100", is_total: true },
  ];
  const neg = (v: unknown) => n(v) < 0 ? { color: "var(--delta-down)" } : undefined;

  // NII waterfall: customer margin down to what the business units keep.
  const niiOption = useMemo(() => {
    if (!v) return null;
    return waterfallOption([
      { label: "Net interest\nincome", value: n(v.net_interest_income), kind: "total" },
      { label: "Treasury\nfunding", value: n(v.treasury_funding_of_gap), kind: "delta" },
      { label: "Liquidity\npremium", value: n(v.liquidity_premium), kind: "delta" },
      { label: "Other\ncost", value: n(v.other_cost), kind: "delta" },
      { label: "Business\nunits", value: n(v.business_units_total), kind: "total" },
    ], t);
  }, [v, t]);

  const toneOf = (s: string) =>
    s === "critical" ? "critical" : s === "serious" ? "critical"
    : s === "warning" ? "warning" : "info";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      {/* --- what needs attention --- */}
      <Card title="Needs attention"
            subtitle={watch.data
              ? `${visibleItems.length} item${visibleItems.length === 1 ? "" : "s"}` +
                (watch.data.critical_count ? ` · ${watch.data.critical_count} critical` : "") +
                (ackedCount ? ` · ${ackedCount} acknowledged` : "")
              : undefined}
            actions={ackedCount > 0
              ? <MiniButton onClick={restore} title="Bring all acknowledged items back">
                  Restore {ackedCount}
                </MiniButton>
              : undefined}
            footnote="Ordered by money at stake rather than by rule, so the largest exposure reads first. Acknowledge an item once and it stays hidden for you until it changes.">
        {!watch.data?.items.length
          ? <Empty title="Nothing outstanding"
                   hint="No stale data, no rejected rows, no loss-making products in this slice." />
          : nothingOutstanding
            ? <Empty title="Everything acknowledged"
                     hint="All items for this slice have been acknowledged." />
          : (
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {visibleItems.map((item, i) => (
                <div key={i} style={{
                  display: "flex", flexWrap: "wrap", gap: "8px 12px",
                  alignItems: "center", padding: "10px 12px", borderRadius: "var(--radius-sm)",
                  background: "var(--surface-2)", border: "1px solid var(--border)",
                  borderLeft: `3px solid ${
                    item.severity === "critical" || item.severity === "serious"
                      ? "var(--status-critical)"
                      : item.severity === "warning" ? "var(--status-warning)"
                      : "var(--accent)"}`,
                }}>
                  <Pill tone={toneOf(item.severity)}>{item.severity}</Pill>
                  <div style={{ minWidth: 0, flex: "1 1 240px" }}>
                    <div style={{ fontSize: "var(--fs-base)", fontWeight: 600 }}>{item.title}</div>
                    <div style={{ fontSize: "var(--fs-sm)", color: "var(--text-secondary)" }}>
                      {item.detail}
                    </div>
                  </div>
                  <div style={{ display: "flex", gap: 8, alignItems: "center", marginLeft: "auto" }}>
                    {item.amount && (
                      <span className="tnum" style={{ fontSize: "var(--fs-base)", fontWeight: 600,
                                                      whiteSpace: "nowrap" }}>
                        {money(item.amount)}
                      </span>
                    )}
                    <MiniButton onClick={() => ack(item)} title="Hide this item">
                      Ack
                    </MiniButton>
                  </div>
                </div>
              ))}
            </div>
          )}
      </Card>

      {/* --- the ratios a bank reports daily --- */}
      <Grid cols="repeat(auto-fit, minmax(160px, 1fr))">
        <Stat label="Yield on advances" value={pct(r?.yield_on_advances_pct, 2)}
              hint="annualised" why={why("yield_on_advances")} />
        <Stat label="Cost of deposits" value={pct(r?.cost_of_deposits_pct, 2)}
              hint="annualised" why={why("cost_of_deposits")} />
        <Stat label="Gross spread" value={pct(r?.gross_spread_pct, 2)}
              hint="yield less cost" tone="good" why={why("spread")} />
        <Stat label="Net interest margin" value={pct(r?.nim_pct, 2)}
              hint="NII over advances" why={why("nim")} />
        <Stat label="CASA ratio" value={pct(r?.casa_ratio_pct, 1)}
              hint="demand over total deposits" why={why("casa")}
              tone={n(r?.casa_ratio_pct) < 30 ? "bad" : "neutral"} />
        <Stat label="Credit-deposit ratio" value={pct(r?.credit_deposit_ratio_pct, 1)}
              why={why("cd_ratio")}
              hint={n(r?.credit_deposit_ratio_pct) > 100
                ? "advances exceed deposits" : "self-funded"}
              tone={n(r?.credit_deposit_ratio_pct) > 100 ? "bad" : "good"} />
      </Grid>

      {/* --- yield on advances, product by product --- */}
      <Card title="Yield on advances by product"
            subtitle={ay?.days
              ? `Annualised; balances averaged over ${ay.days} day${ay.days === 1 ? "" : "s"} · click a product to filter the page`
              : undefined}
            footnote="Yield on advances is interest received over loan balance, annualised on the same basis as the tile above, so the total row equals it. FTP rate is the spread each product keeps after treasury charges it for funding; a negative figure means the product earns less than its funding costs.">
        {advanceYield.error ? <Empty title="Could not load" hint={advanceYield.error} />
          : (
            <>
            <SortButtons sort={aySort} options={AY_SORTS} />
            <Table rows={ayRows}
                   csvName="ftp-yield-on-advances-by-product.csv"
                   onRowClick={(x) => { if (!x.is_total) setFilters((f) => ({ ...f, product_code: [x.product_code] })); }}
                   empty="No loan products in this selection."
                   cols={[
                     { key: "p", label: "Product",
                       render: (x) => x.is_total ? <b>Total</b>
                         : <span><b>{x.product_code}</b>{" "}
                             <span style={{ color: "var(--text-muted)" }}>{x.short_name}</span></span>,
                       value: (x) => x.product_code },
                     { key: "a", label: "Accounts", align: "right",
                       render: (x) => n(x.avg_accounts).toLocaleString(undefined, { maximumFractionDigits: 0 }),
                       value: (x) => x.avg_accounts },
                     { key: "b", label: "Avg balance", align: "right",
                       render: (x) => compact(x.avg_balance), value: (x) => x.avg_balance },
                     { key: "s", label: "Share", align: "right",
                       render: (x) => pct(x.share_pct, 1), value: (x) => x.share_pct },
                     { key: "y", label: "Yield on advances", align: "right",
                       render: (x) => x.is_total ? <b>{pct(x.yield_pct, 2)}</b> : pct(x.yield_pct, 2),
                       value: (x) => x.yield_pct },
                     { key: "i", label: "Interest received", align: "right",
                       render: (x) => money(x.interest_received), value: (x) => x.interest_received },
                     { key: "r", label: "FTP rate", align: "right",
                       render: (x) => <span style={neg(x.ftp_rate_pct)}>{pct(x.ftp_rate_pct, 2)}</span>,
                       value: (x) => x.ftp_rate_pct },
                     { key: "f", label: "FTP profit", align: "right",
                       render: (x) => <span style={neg(x.ftp_profit)}>{money(x.ftp_profit)}</span>,
                       value: (x) => x.ftp_profit },
                   ]} />
            </>
          )}
      </Card>

      {/* --- cost of deposits, product by product --- */}
      <Card title="Cost of deposits by product"
            subtitle={dc?.days
              ? `Annualised; balances averaged over ${dc.days} day${dc.days === 1 ? "" : "s"} · click a product to filter the page`
              : undefined}
            footnote="Cost of deposits is interest paid over deposit balance, annualised on the same basis as the tile above, so the total row equals it. FTP rate is what treasury credits each product for its funding after the customer rate; a negative figure means the product pays depositors more than its funding is worth.">
        {depositCost.error ? <Empty title="Could not load" hint={depositCost.error} />
          : (
            <>
            <SortButtons sort={dcSort} options={DC_SORTS} />
            <Table rows={dcRows}
                   csvName="ftp-cost-of-deposits-by-product.csv"
                   onRowClick={(x) => { if (!x.is_total) setFilters((f) => ({ ...f, product_code: [x.product_code] })); }}
                   empty="No deposit products in this selection."
                   cols={[
                     { key: "p", label: "Product",
                       render: (x) => x.is_total ? <b>Total</b>
                         : <span><b>{x.product_code}</b>{" "}
                             <span style={{ color: "var(--text-muted)" }}>{x.short_name}</span></span>,
                       value: (x) => x.product_code },
                     { key: "t", label: "Type",
                       render: (x) => x.liability_nature === "DEMAND" ? "CASA"
                         : x.liability_nature === "TIME" ? "Term" : "",
                       value: (x) => x.liability_nature ?? "" },
                     { key: "a", label: "Accounts", align: "right",
                       render: (x) => n(x.avg_accounts).toLocaleString(undefined, { maximumFractionDigits: 0 }),
                       value: (x) => x.avg_accounts },
                     { key: "b", label: "Avg balance", align: "right",
                       render: (x) => compact(x.avg_balance), value: (x) => x.avg_balance },
                     { key: "s", label: "Share", align: "right",
                       render: (x) => pct(x.share_pct, 1), value: (x) => x.share_pct },
                     { key: "c", label: "Cost of deposits", align: "right",
                       render: (x) => x.is_total ? <b>{pct(x.cost_pct, 2)}</b> : pct(x.cost_pct, 2),
                       value: (x) => x.cost_pct },
                     { key: "i", label: "Interest paid", align: "right",
                       render: (x) => money(x.interest_paid), value: (x) => x.interest_paid },
                     { key: "r", label: "FTP rate", align: "right",
                       render: (x) => <span style={neg(x.ftp_rate_pct)}>{pct(x.ftp_rate_pct, 2)}</span>,
                       value: (x) => x.ftp_rate_pct },
                     { key: "f", label: "FTP profit", align: "right",
                       render: (x) => <span style={neg(x.ftp_profit)}>{money(x.ftp_profit)}</span>,
                       value: (x) => x.ftp_profit },
                   ]} />
            </>
          )}
      </Card>

      <Grid cols="minmax(0, 1.25fr) minmax(0, 1fr)">
        {/* --- NII reconciliation --- */}
        <Card expandable title="Where the customer margin goes"
              subtitle="Net interest income split between business units and treasury"
              footnote={!v ? undefined
                : v.nii_is_negative
                  ? `Net interest income is negative: this book pays more to depositors than it earns from borrowers, so there is no margin to share out. Business units still show ${money(v.business_units_total)} because FTP credits deposits for the funding they provide; the treasury carries the shortfall. Reconciliation check ${v.check}.`
                  : v.business_units_share_pct == null
                    ? `The treasury retains ${money(v.treasury_retained)} — mostly the cost of funding the net asset position. Reconciliation check ${v.check}.`
                    : `Business units keep ${pct(v.business_units_share_pct, 1)} of NII. The treasury retains ${money(v.treasury_retained)} — mostly the cost of funding the net asset position. Reconciliation check ${v.check}.`}>
          {!niiOption ? <Empty title="No data" />
            : <Chart option={niiOption} height={262} loading={nii.loading}
                     ariaLabel="Net interest income reconciled to FTP" />}
        </Card>

        {/* --- the split, as numbers --- */}
        <Card title="Margin allocation"
              subtitle="Every figure below reconciles to the chart">
          {!v ? <Empty title="No data" />
            : (
              <dl style={{ margin: 0, display: "grid",
                           gridTemplateColumns: "1fr auto", rowGap: 7, fontSize: "var(--fs-base)",
                           padding: "4px 4px 0" }}>
                <Line label="Interest received" value={money(v.interest_received)} />
                <Line label="Interest paid" value={`(${money(v.interest_paid)})`} />
                <Line label="Net interest income" value={money(v.net_interest_income)} bold />
                <Divider />
                <Line label="Lending spread (asset)" value={money(v.lending_spread)} />
                <Line label="Deposit spread (liability)" value={money(v.deposit_spread)} />
                <Line label="Business units total" value={money(v.business_units_total)} bold />
                <Divider />
                <Line label="Funding of net position" value={money(v.treasury_funding_of_gap)} />
                <Line label="Liquidity premium" value={money(v.liquidity_premium)} />
                <Line label="Other cost" value={money(v.other_cost)} />
                <Line label="Treasury retained" value={money(v.treasury_retained)} bold />
              </dl>
            )}
        </Card>
      </Grid>

      <Grid cols="minmax(0, 1fr) minmax(0, 1.2fr)">
        {/* --- MTD / QTD / YTD --- */}
        <Card title="Period to date"
              subtitle={periods.data?.as_of ? `as of ${longDate(periods.data.as_of)}` : undefined}>
          <Table rows={periods.data?.periods ?? []}
                 cols={[
                   { key: "label", label: "Period",
                     render: (p) => <b>{p.label}</b>, value: (p) => p.label },
                   { key: "d", label: "Days", align: "right", render: (p) => String(p.days),
                     value: (p) => p.days },
                   { key: "net", label: "Net FTP", align: "right",
                     render: (p) => money(p.net_ftp_profit), value: (p) => p.net_ftp_profit },
                   { key: "avg", label: "Avg / day", align: "right",
                     render: (p) => money(p.avg_daily), value: (p) => p.avg_daily },
                 ]} />
          <p style={{ margin: "10px 4px 0", fontSize: "var(--fs-sm)", color: "var(--text-muted)" }}>
            Balance sheet: advances {compact(r?.advances)} against deposits{" "}
            {compact(r?.deposits)}, a funding gap of {compact(r?.funding_gap)}.
          </p>
        </Card>

        {/* --- repricing opportunity --- */}
        <Card title="Repricing opportunity"
              subtitle={repricing.data
                ? `${repricing.data.accounts_below_median.toLocaleString()} account-days below their product median`
                : undefined}
              footnote="Measured against each product's median spread, not its mean: a few deeply mispriced accounts would drag a mean target down and understate the prize.">
          {!repricing.data?.accounts_below_median
            ? <Empty title="Nothing below median" />
            : (
              <>
                <Grid cols="repeat(auto-fit, minmax(130px, 1fr))" gap={10}>
                  <Stat label="Opportunity" value={money(repricing.data.opportunity)}
                        tone="good"
                        hint={`+${pct(repricing.data.uplift_pct, 1)} on current`} />
                  <Stat label="Balance affected"
                        value={compact(repricing.data.balance_below_median)} />
                </Grid>
                <div style={{ marginTop: 10 }}>
                  <Table rows={repricing.data.by_product}
                         csvName="ftp-repricing-by-product.csv"
                         cols={[
                           { key: "p", label: "Product", render: (x) => x.product_code,
                             value: (x) => x.product_code },
                           { key: "a", label: "Account-days", align: "right",
                             render: (x) => x.accounts.toLocaleString(),
                             value: (x) => x.accounts },
                           { key: "o", label: "Upside", align: "right",
                             render: (x) => money(x.opportunity),
                             value: (x) => x.opportunity },
                         ]} />
                </div>
              </>
            )}
        </Card>
      </Grid>

      {summary.data?.comparison && (
        <p style={{ margin: 0, fontSize: "var(--fs-sm)", color: "var(--text-secondary)", textAlign: "center" }}>
          {summary.data.comparison.prior_has_data
            ? `Comparing ${longDate(summary.data.comparison.current_period.start)} – ` +
              `${longDate(summary.data.comparison.current_period.end)} against the ` +
              `preceding ${summary.data.comparison.prior_period.days} days.`
            : `Showing ${longDate(summary.data.comparison.current_period.start)} – ` +
              `${longDate(summary.data.comparison.current_period.end)}. No earlier ` +
              `data is loaded, so no period comparison is shown.`}
        </p>
      )}
    </div>
  );
}

// -- sorting the by-product tables ------------------------------------------ #
// Sorted in the browser: the rows are already on the page, and a product list
// is short. The Total row is appended after sorting so it always reads last.

type DcKey = "avg_balance" | "cost_pct" | "interest_paid" | "ftp_rate_pct" | "ftp_profit";
type AyKey = "avg_balance" | "yield_pct" | "interest_received" | "ftp_rate_pct" | "ftp_profit";

const DC_SORTS: [DcKey, string][] = [
  ["avg_balance", "Balance"], ["cost_pct", "Cost of deposits"],
  ["interest_paid", "Interest paid"], ["ftp_rate_pct", "FTP rate"],
  ["ftp_profit", "FTP profit"],
];
const AY_SORTS: [AyKey, string][] = [
  ["avg_balance", "Balance"], ["yield_pct", "Yield"],
  ["interest_received", "Interest received"], ["ftp_rate_pct", "FTP rate"],
  ["ftp_profit", "FTP profit"],
];

function useSort<K extends string>(initial: K) {
  const [key, setKey] = useState<K>(initial);
  const [desc, setDesc] = useState(true);
  const by = (k: K) => {
    if (k === key) setDesc((d) => !d);
    else { setKey(k); setDesc(true); }
  };
  // A product with no rate (no balance) sorts last in either direction.
  const apply = <T extends Record<K, unknown>>(rows: T[]): T[] =>
    [...rows].sort((a, b) => {
      const av = a[key], bv = b[key];
      if (av == null) return bv == null ? 0 : 1;
      if (bv == null) return -1;
      return desc ? n(bv) - n(av) : n(av) - n(bv);
    });
  return { key, desc, by, apply };
}

function SortButtons<K extends string>({ sort, options }: {
  sort: ReturnType<typeof useSort<K>>; options: [K, string][];
}) {
  return (
    <div style={{ display: "flex", gap: 6, marginBottom: 8, flexWrap: "wrap",
                  alignItems: "center" }}>
      <span style={{ fontSize: "var(--fs-sm)", color: "var(--text-muted)" }}>Sort by</span>
      {options.map(([k, lbl]) => (
        <MiniButton key={k} active={sort.key === k} onClick={() => sort.by(k)}
                    title={sort.key === k ? "Click again to reverse" : undefined}>
          {lbl}{sort.key === k ? (sort.desc ? " ↓" : " ↑") : ""}
        </MiniButton>
      ))}
    </div>
  );
}

function Line({ label, value, bold }: { label: string; value: string; bold?: boolean }) {
  return (
    <>
      <dt style={{ color: bold ? "var(--text-primary)" : "var(--text-muted)",
                   fontWeight: bold ? 600 : 400 }}>{label}</dt>
      <dd className="tnum" style={{ margin: 0, textAlign: "right",
                                    color: "var(--text-primary)",
                                    fontWeight: bold ? 600 : 400 }}>{value}</dd>
    </>
  );
}

function Divider() {
  return <div style={{ gridColumn: "1 / -1", height: 1, background: "var(--grid)",
                       margin: "3px 0" }} />;
}

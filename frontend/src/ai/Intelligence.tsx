import { useMemo, useState } from "react";
import Chart, { axisCommon, baseOption, useTokens } from "../components/Chart";
import { Button, Card, Empty, Grid, MiniButton, Pill, Stat, Table } from "../components/ui";
import { compact, shortDate } from "../format";
import { useApp, useAsync } from "../state";
import {
  BenchmarkRow, marketApi, MarketOverview, MarketSeries, NewsItem, ParseResult,
} from "./api";

const hint: React.CSSProperties = {
  fontSize: "var(--fs-xs)", color: "var(--text-muted)", margin: "4px 0 0", lineHeight: 1.45,
};
const para: React.CSSProperties = {
  margin: 0, fontSize: "var(--fs-base)", color: "var(--text-secondary)", lineHeight: 1.55,
};

//: Tiles, in reading order: the corridor, the short end, the curve, then the world.
const PULSE = ["BB_POLICY", "BB_CALL_ON", "BB_TBILL_91", "BB_TBILL_364", "BB_TBOND_5Y",
               "FX_USDBDT", "US_FEDFUNDS", "US_UST_10Y", "BRENT"];

const TENOR_LABEL: Record<number, string> = {
  1: "O/N", 7: "1W", 30: "1M", 91: "3M", 182: "6M", 364: "1Y", 730: "2Y", 1095: "3Y",
  1825: "5Y", 3650: "10Y", 5475: "15Y", 7300: "20Y",
};
const TENORS = [1, 7, 30, 91, 182, 364, 730, 1095, 1825, 3650, 5475, 7300];
const tenorLabel = (d: number) => TENOR_LABEL[d] ?? (d < 365 ? `${d}D` : `${Math.round(d / 365)}Y`);

const fmtValue = (s: MarketSeries) => {
  if (s.value == null) return "—";
  if (s.unit === "pct") return `${Number(s.value).toFixed(2)}%`;
  if (s.unit === "bdt") return `৳${Number(s.value).toFixed(2)}`;
  if (s.unit === "usd") return `$${Number(s.value).toFixed(2)}`;
  if (s.unit === "bdt_cr") return `${compact(Number(s.value) * 1e7)}`;
  return Number(s.value).toFixed(2);
};
const fmtChange = (s: MarketSeries) => {
  if (s.change == null) return null;
  const c = Number(s.change);
  if (s.unit === "pct") return `${c > 0 ? "+" : ""}${Math.round(c * 100)} bp`;
  return `${c > 0 ? "+" : ""}${c.toFixed(2)}`;
};

const TAGS: [string, string][] = [
  ["", "All"], ["policy_rate", "Policy rate"], ["liquidity", "Liquidity"],
  ["govt_securities", "T-bills & bonds"], ["deposits", "Deposits"], ["credit", "Credit"],
  ["remittance", "Remittance"], ["inflation", "Inflation"], ["fx", "FX"],
  ["global_rates", "Global rates"],
];

function ago(iso: string | null) {
  if (!iso) return "";
  const m = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (m < 60) return `${Math.max(m, 1)} min ago`;
  if (m < 60 * 24) return `${Math.round(m / 60)} h ago`;
  return `${Math.round(m / 1440)} d ago`;
}

export default function Intelligence() {
  const { can, me, ai } = useApp();
  const [tick, setTick] = useState(0);
  const ov = useAsync(() => marketApi.overview(), [tick]);
  const [refreshing, setRefreshing] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const editor = can("AI_MARKET_EDIT") && me?.scope_level === "HO";

  const refresh = async () => {
    setRefreshing(true); setMsg(null);
    try {
      const r = await marketApi.refresh();
      setMsg(r.results.map((x) => `${x.job.replace("market_", "")}: ${x.status}`).join(" · "));
      setTick((t) => t + 1);
    } catch (e) { setMsg((e as Error).message); } finally { setRefreshing(false); }
  };

  if (ov.error) return <Card><Empty title="Could not load market data" hint={ov.error} /></Card>;
  const d = ov.data;
  if (!d) return <p style={{ color: "var(--text-muted)" }}>Loading…</p>;

  const byCode = Object.fromEntries(d.series.map((s) => [s.code, s]));
  const bdMissing = d.series.filter((s) => s.source === "bb_paste" && s.value == null
                                           && ["BB_CALL_ON", "BB_TBILL_91", "BB_POLICY"].includes(s.code));
  const prices = d.jobs.market_prices;
  const newsJob = d.jobs.market_news;
  const bbJob = d.jobs.market_bb;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      {/* --- freshness ---------------------------------------------------- */}
      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap",
                    fontSize: "var(--fs-sm)", color: "var(--text-muted)" }}>
        <Pill tone={bbJob?.last_status === "blocked" ? "critical" : bbJob?.last_success ? "good" : "warning"}>
          Bangladesh Bank {bbJob?.last_status === "blocked" ? "paused: paste rates below"
            : bbJob?.last_success ? `read ${ago(bbJob.last_success)}` : "not read yet"}
        </Pill>
        <Pill tone={prices.last_success ? "good" : "warning"}>
          Global prices {prices.last_success ? `updated ${ago(prices.last_success)}` : "not collected yet"}
        </Pill>
        <Pill tone={newsJob.last_success ? "good" : "warning"}>
          News {newsJob.last_success ? `updated ${ago(newsJob.last_success)}` : "not collected yet"}
        </Pill>
        {(prices.errors?.length || newsJob.errors?.length || bbJob?.errors?.length) ? (
          <span title={[...(prices.errors ?? []), ...(newsJob.errors ?? []), ...(bbJob?.errors ?? [])].join("\n")}>
            <Pill tone="warning">Some sources failed</Pill></span>) : null}
        <span style={{ flex: 1 }} />
        {msg && <span>{msg}</span>}
        {ai?.can_admin && <MiniButton icon="refresh" disabled={refreshing} onClick={refresh}>
          {refreshing ? "Collecting…" : "Collect now"}</MiniButton>}
      </div>

      {bdMissing.length > 0 && (
        <div style={{ padding: "10px 12px", borderRadius: 8, border: "1px solid var(--status-warning)",
                      background: "var(--surface-2)", fontSize: "var(--fs-base)" }}>
          <Pill tone="warning">Bangladesh rates missing</Pill>{" "}
          <span style={{ color: "var(--text-secondary)" }}>
            {bdMissing.map((s) => s.short).join(", ")} {bdMissing.length === 1 ? "has" : "have"} no
            value yet. They are read from bb.org.bd every two hours in Dhaka business hours;
            {editor ? " to fill them now, use Collect now or paste the page below." :
              " head-office treasury can also enter them."}
          </span>
        </div>
      )}

      {/* --- market pulse ------------------------------------------------- */}
      <Grid cols="repeat(auto-fill, minmax(190px, 1fr))" gap={12}>
        {PULSE.map((c) => byCode[c]).filter(Boolean).map((s) => (
          // A market move is neither good nor bad, so it is stated in the note
          // rather than as a coloured delta.
          <Stat key={s.code} label={s.short} value={fmtValue(s)}
                spark={s.spark.length > 2 ? s.spark.map(Number) : undefined}
                hint={s.as_of
                  ? [fmtChange(s) && s.previous_as_of && `${fmtChange(s)} since ${shortDate(s.previous_as_of)}`,
                     `${s.stale ? "stale · " : ""}as of ${shortDate(s.as_of)}`].filter(Boolean).join(" · ")
                  : "no data yet"} />
        ))}
      </Grid>

      <CurveCard d={d} />
      <BenchmarkCard d={d} />
      {editor && <EnterRates onSaved={() => setTick((t) => t + 1)} />}
      <NewsCard />

      <p style={{ ...hint, textAlign: "center" }}>
        Bangladesh rates: bb.org.bd, read automatically or entered by treasury. Global rates: FRED, Federal Reserve
        Bank of St. Louis. <a href="https://www.exchangerate-api.com" target="_blank"
        rel="noreferrer">Rates By Exchange Rate API</a> (market mid, not Bangladesh Bank's official rate).
      </p>
    </div>
  );
}

// --------------------------------------------------------------------------
// The taka curve with the bank's benchmarks on it
// --------------------------------------------------------------------------

function CurveCard({ d }: { d: MarketOverview }) {
  const t = useTokens();
  const items = d.benchmarks.items.filter((b) => b.benchmark != null);

  const option = useMemo(() => {
    // Evenly spaced tenors, as yield curves are read; a gap stays a gap.
    const tenors = TENORS.filter((days) =>
      d.curve.some((p) => p.tenor_days === days) || items.some((b) => b.tenor_days === days));
    const cat = tenors.map(tenorLabel);
    const at = (days: number) => tenors.indexOf(days);
    const curvePt = new Map(d.curve.map((p) => [p.tenor_days, p]));
    const curve = tenors.map((days) => {
      const p = curvePt.get(days);
      return p ? { value: [at(days), Number(p.value)], name: p.label, extra: p.as_of } : null;
    });
    const pts = (side: string) => items.filter((b) => b.side === side && at(b.tenor_days) >= 0)
      .map((b) => ({ value: [at(b.tenor_days), Number(b.benchmark)], name: b.name,
                     extra: b.gap_bp, market: b.market }));
    const all = [...d.curve.map((c) => Number(c.value)), ...items.map((b) => Number(b.benchmark))];
    const lo = Math.floor(Math.min(...all) - 0.5), hi = Math.ceil(Math.max(...all) + 0.5);
    const ring = { borderColor: t.surface, borderWidth: 2 };
    return {
      ...baseOption(t),
      grid: { left: 8, right: 24, top: 40, bottom: 8, containLabel: true },
      legend: { ...baseOption(t).legend, top: 0, left: 0 },
      tooltip: {
        ...baseOption(t).tooltip, trigger: "item" as const,
        formatter: (raw: never) => {
          const p = raw as unknown as { seriesName: string;
            data: { value: number[]; name: string; extra: string | number | null; market?: number } };
          const { value: [i, v], name, extra } = p.data;
          if (p.seriesName === "Market curve")
            return `<b>${name}</b> · ${cat[i]}<br/>${v.toFixed(2)}% (as of ${extra})`;
          const gap = extra == null ? "" : `<br/>${(extra as number) > 0 ? "+" : ""}${extra} bp vs market ${Number(p.data.market).toFixed(2)}%`;
          return `<b>${name}</b><br/>FTP benchmark ${v.toFixed(2)}% at ${cat[i]}${gap}`;
        },
      },
      xAxis: { type: "category" as const, data: cat, boundaryGap: false, ...axisCommon(t),
               splitLine: { show: false } },
      yAxis: { type: "value" as const, min: lo, max: hi, ...axisCommon(t), axisLine: { show: false },
               axisLabel: { color: t.muted, fontSize: 11, formatter: "{value}%" } },
      series: [
        { name: "Market curve", type: "line" as const, data: curve, connectNulls: true,
          symbol: "circle", symbolSize: 8, lineStyle: { width: 2, color: t.series[0] },
          itemStyle: { color: t.series[0], ...ring }, z: 3 },
        { name: "Deposit benchmarks", type: "scatter" as const, data: pts("LIABILITY"), symbolSize: 10,
          itemStyle: { color: t.series[1], ...ring }, z: 4 },
        { name: "Loan benchmarks", type: "scatter" as const, data: pts("ASSET"), symbol: "diamond",
          symbolSize: 11, itemStyle: { color: t.series[2], ...ring }, z: 4 },
      ],
    } as never;
  }, [d, items, t]);

  return (
    <Card title="Taka curve and your FTP benchmarks" expandable
          subtitle="Market rates by term, with each product's configured benchmark at the term it funds"
          footnote={d.curve.length ? `Curve: ${d.curve.map((p) => `${p.label} ${Number(p.value).toFixed(2)}%`).join(" · ")}`
            : "No Bangladesh curve points yet."}>
      {d.curve.length < 2
        ? <Empty title="Not enough of the curve yet"
                 hint="Enter call money and treasury auction results to draw the curve." />
        : <Chart option={option} height={320}
                 ariaLabel="Taka market curve by tenor with product FTP benchmarks" />}
    </Card>
  );
}

// --------------------------------------------------------------------------
// Benchmark vs market, product by product
// --------------------------------------------------------------------------

function BenchmarkCard({ d }: { d: MarketOverview }) {
  const rows = [...d.benchmarks.items].sort((a, b) =>
    Math.abs(Number(b.monthly_impact ?? 0)) - Math.abs(Number(a.monthly_impact ?? 0))
    || Math.abs(b.gap_bp ?? 0) - Math.abs(a.gap_bp ?? 0));
  const withBalances = d.benchmarks.balances_as_of != null;

  const meaning = (r: BenchmarkRow) => {
    if (r.gap_bp == null) return "";
    if (Math.abs(r.gap_bp) < 10) return "In line with the market";
    const below = r.gap_bp < 0;
    if (r.side === "LIABILITY")
      return below ? "Deposits credited below market value" : "Deposits credited above market value";
    return below ? "Loans charged below the market cost of funds" : "Loans charged above the market cost of funds";
  };

  return (
    <Card title="Benchmarks against the market"
          subtitle={withBalances
            ? `Largest monthly effect first, on balances of ${shortDate(d.benchmarks.balances_as_of!)}`
            : "Largest gap first"}
          footnote="Tenors are inferred from product names and types; non-maturity deposits use a one-year behavioural tenor. A gap is a question for ALCO, not automatically an error.">
      {d.benchmarks.error && <p style={para}>{d.benchmarks.error}</p>}
      <Table<BenchmarkRow> rows={rows} csvName="benchmarks-vs-market" search={(r) => `${r.product_code} ${r.name}`}
        cols={[
          { key: "p", label: "Product", render: (r) => (
            <div><div style={{ fontWeight: 600 }}>{r.name}</div>
              <div style={{ fontSize: "var(--fs-xs)", color: "var(--text-muted)" }}>
                {r.product_code} · {r.side === "LIABILITY" ? "deposit" : "loan"}</div></div>) },
          { key: "t", label: "Term", render: (r) => (
            <span title={r.tenor_basis}>{tenorLabel(r.tenor_days)}{" "}
              {r.behavioural && <Pill tone="neutral">behavioural</Pill>}</span>) },
          { key: "b", label: "Benchmark", align: "right", value: (r) => r.benchmark,
            render: (r) => r.benchmark == null ? "—" : `${Number(r.benchmark).toFixed(2)}%` },
          { key: "m", label: "Market", align: "right", value: (r) => r.market,
            render: (r) => r.market == null ? "—"
              : <span title={r.market_basis ?? undefined}>{Number(r.market).toFixed(2)}%</span> },
          { key: "g", label: "Gap", align: "right", value: (r) => r.gap_bp,
            render: (r) => r.gap_bp == null ? "—" : (
              <b className="tnum">{r.gap_bp > 0 ? "+" : ""}{r.gap_bp} bp</b>) },
          ...(withBalances ? [{ key: "i", label: "Per month", align: "right" as const,
            value: (r: BenchmarkRow) => r.monthly_impact,
            render: (r: BenchmarkRow) => r.monthly_impact == null ? "—"
              : <span className="tnum">৳{compact(Math.abs(Number(r.monthly_impact)))}</span> }] : []),
          { key: "w", label: "What it means", render: (r) => (
            <span style={{ color: "var(--text-secondary)", fontSize: "var(--fs-sm)" }}>
              {meaning(r)}</span>) },
        ]} />
    </Card>
  );
}

// --------------------------------------------------------------------------
// Treasury: paste the Bangladesh Bank page
// --------------------------------------------------------------------------

const BB_PAGES: [string, string][] = [
  ["Call money market", "https://www.bb.org.bd/en/index.php/monetaryactivity/call_money_market"],
  ["Money market reference rates", "https://www.bb.org.bd/en/index.php/monetaryactivity/money_market_ref_rate"],
  ["Treasury bill & bond auctions", "https://www.bb.org.bd/en/index.php/monetaryactivity/treasury"],
];

function EnterRates({ onSaved }: { onSaved: () => void }) {
  const [text, setText] = useState("");
  const [parsed, setParsed] = useState<ParseResult | null>(null);
  const [keep, setKeep] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ tone: "good" | "critical"; text: string } | null>(null);
  const [policy, setPolicy] = useState({ BB_POLICY: "", BB_SLF: "", BB_SDF: "",
                                          date: new Date().toISOString().slice(0, 10) });

  const read = async () => {
    setBusy(true); setMsg(null); setParsed(null);
    try {
      const r = await marketApi.parse(text);
      setParsed(r);
      setKeep(Object.fromEntries(r.items.map((i) => [i.code, true])));
    } catch (e) { setMsg({ tone: "critical", text: (e as Error).message }); }
    finally { setBusy(false); }
  };

  const save = async () => {
    if (!parsed) return;
    const entries = parsed.items.filter((i) => keep[i.code] && i.obs_date)
      .map((i) => ({ code: i.code, obs_date: i.obs_date!, value: i.value, ref: i.evidence }));
    setBusy(true);
    try {
      const r = await marketApi.save("bb_paste", entries);
      setMsg({ tone: "good", text: `Saved ${r.saved} rate${r.saved === 1 ? "" : "s"} (${r.changed} new or changed).` });
      setParsed(null); setText(""); onSaved();
    } catch (e) { setMsg({ tone: "critical", text: (e as Error).message }); }
    finally { setBusy(false); }
  };

  const savePolicy = async () => {
    const entries = (["BB_POLICY", "BB_SLF", "BB_SDF"] as const)
      .filter((c) => policy[c].trim())
      .map((c) => ({ code: c, obs_date: policy.date, value: policy[c].trim(), ref: "Entered by treasury" }));
    if (!entries.length) return;
    setBusy(true);
    try {
      await marketApi.save("manual", entries);
      setMsg({ tone: "good", text: "Policy rates saved." });
      setPolicy({ ...policy, BB_POLICY: "", BB_SLF: "", BB_SDF: "" }); onSaved();
    } catch (e) { setMsg({ tone: "critical", text: (e as Error).message }); }
    finally { setBusy(false); }
  };

  return (
    <Card title="Enter Bangladesh Bank rates"
          subtitle="Rates are read from bb.org.bd automatically. Use this if a page could not be read, or to correct a value: a rate entered here is never overwritten by the automatic reader">
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <p style={para}>
          1. Open a Bangladesh Bank page:{" "}
          {BB_PAGES.map(([l, u], i) => (<span key={u}>{i > 0 && " · "}
            <a href={u} target="_blank" rel="noreferrer">{l} ↗</a></span>))}
          <br />2. Select the whole page (Ctrl+A / ⌘A), copy it, and paste it here.
          <br />3. Check the rates it found, then save.
        </p>
        <textarea value={text} onChange={(e) => setText(e.target.value)} rows={4}
                  placeholder="Paste the Bangladesh Bank page here…"
                  style={{ width: "100%", resize: "vertical", font: "inherit", padding: "8px 10px" }} />
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <Button variant="primary" disabled={busy || text.trim().length < 10} onClick={read}>
            {busy && !parsed ? "Reading…" : "Read rates"}</Button>
          {msg && <span style={para}><Pill tone={msg.tone}>{msg.tone === "good" ? "Done" : "Refused"}</Pill> {msg.text}</span>}
        </div>

        {parsed && (
          <div style={{ borderTop: "1px solid var(--border)", paddingTop: 12,
                        display: "flex", flexDirection: "column", gap: 10 }}>
            <p style={para}>
              Read as <b>{{ call_money: "call money market", ref_rates: "money market reference rates",
                auctions: "treasury auction results", unknown: "an unrecognised page" }[parsed.kind]}</b>
              {parsed.page_date && <> for {shortDate(parsed.page_date)}</>}.
            </p>
            {parsed.warnings.map((w) => <p key={w} style={para}><Pill tone="warning">Check</Pill> {w}</p>)}
            {parsed.items.length > 0 && (
              <Table rows={parsed.items} cols={[
                { key: "k", label: "Save", render: (i) => (
                  <input type="checkbox" aria-label={`Save ${i.name}`} checked={keep[i.code] ?? false}
                         onChange={(e) => setKeep({ ...keep, [i.code]: e.target.checked })} />) },
                { key: "n", label: "Rate", render: (i) => i.name },
                { key: "d", label: "Date", render: (i) => i.obs_date ? shortDate(i.obs_date) : "—" },
                { key: "v", label: "Value", align: "right", render: (i) => (
                  <b className="tnum">{i.unit === "pct" ? `${Number(i.value).toFixed(4)}%` : Number(i.value).toFixed(2)}</b>) },
                { key: "c", label: "Current", align: "right", render: (i) => i.current == null ? "—"
                  : <span className="tnum" style={{ color: "var(--text-muted)" }}>
                      {i.unit === "pct" ? `${Number(i.current).toFixed(4)}%` : Number(i.current).toFixed(2)}
                      {i.current_as_of && ` (${shortDate(i.current_as_of)})`}</span> },
                { key: "e", label: "Read from", render: (i) => (
                  <span style={{ fontSize: "var(--fs-sm)", color: "var(--text-muted)" }}>{i.evidence}</span>) },
              ]} />
            )}
            <div style={{ display: "flex", gap: 8 }}>
              <Button variant="primary" disabled={busy || !parsed.items.some((i) => keep[i.code])}
                      onClick={save}>Save checked rates</Button>
              <Button onClick={() => setParsed(null)}>Discard</Button>
            </div>
          </div>
        )}

        <div style={{ borderTop: "1px solid var(--border)", paddingTop: 12 }}>
          <p style={{ ...para, marginBottom: 8 }}>Policy corridor (changes at monetary policy
            statements; not on these pages):</p>
          <Grid cols="repeat(auto-fit, minmax(140px, 1fr))" gap={10}>
            {([["BB_POLICY", "Policy rate %"], ["BB_SLF", "SLF %"], ["BB_SDF", "SDF %"]] as const).map(([c, l]) => (
              <label key={c}><span style={hint}>{l}</span>
                <input style={{ width: "100%" }} inputMode="decimal" value={policy[c]}
                       onChange={(e) => setPolicy({ ...policy, [c]: e.target.value.replace(/[^\d.]/g, "") })} />
              </label>))}
            <label><span style={hint}>Effective date</span>
              <input style={{ width: "100%" }} type="date" value={policy.date}
                     onChange={(e) => setPolicy({ ...policy, date: e.target.value })} /></label>
          </Grid>
          <div style={{ marginTop: 8 }}>
            <Button disabled={busy || !(policy.BB_POLICY || policy.BB_SLF || policy.BB_SDF)}
                    onClick={savePolicy}>Save policy rates</Button>
          </div>
        </div>
      </div>
    </Card>
  );
}

// --------------------------------------------------------------------------
// News radar
// --------------------------------------------------------------------------

function NewsCard() {
  const [tag, setTag] = useState("");
  const [region, setRegion] = useState<"" | "BD" | "GLOBAL">("BD");
  const [sort, setSort] = useState<"top" | "latest">("top");
  const news = useAsync(() => marketApi.news({ tag, region, sort, limit: 30 }), [tag, region, sort]);

  const arrow = (s: number) => s > 0 ? <span title="Rates or pressure rising">↑</span>
    : s < 0 ? <span title="Rates or pressure easing">↓</span> : null;

  return (
    <Card title="News radar" subtitle="Headlines ranked by what they mean for an FTP book, linked to the source"
          actions={<div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
            <div style={{ display: "flex", gap: 2 }}>
              <MiniButton active={sort === "top"} onClick={() => setSort("top")}
                          title="Most relevant to an FTP book this week">Top</MiniButton>
              <MiniButton active={sort === "latest"} onClick={() => setSort("latest")}>Latest</MiniButton>
            </div>
            <div style={{ display: "flex", gap: 2 }}>
              {([["BD", "Bangladesh"], ["GLOBAL", "Global"], ["", "All"]] as const).map(([v, l]) => (
                <MiniButton key={v} active={region === v} onClick={() => setRegion(v)}>{l}</MiniButton>))}
            </div>
          </div>}>
      <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginBottom: 10 }}>
        {TAGS.map(([v, l]) => <MiniButton key={v} active={tag === v} onClick={() => setTag(v)}>{l}</MiniButton>)}
      </div>
      {news.error && <p style={para}>{news.error}</p>}
      {news.data && news.data.items.length === 0 && <Empty title="No stories yet"
        hint="News is collected every 15 minutes while AI is on." />}
      <div style={{ display: "flex", flexDirection: "column" }}>
        {news.data?.items.map((n: NewsItem) => (
          <article key={n.id} style={{ padding: "10px 2px", borderTop: "1px solid var(--border)" }}>
            <div style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap",
                          fontSize: "var(--fs-xs)", color: "var(--text-muted)" }}>
              <b style={{ color: "var(--text-secondary)" }}>{n.source}</b>
              <span>{ago(n.published_at)}</span>
              {n.impacts.slice(0, 3).map((i) => <Pill key={i} tone="neutral">{i}</Pill>)}
            </div>
            <a href={n.url} target="_blank" rel="noreferrer"
               style={{ display: "block", marginTop: 3, fontSize: "var(--fs-md)", fontWeight: 600,
                        color: "var(--text-primary)", textDecoration: "none", lineHeight: 1.4 }}>
              {arrow(n.rate_signal)} {n.title}
            </a>
            {n.summary && n.summary !== n.title && (
              <p style={{ ...hint, marginTop: 2 }}>{n.summary.slice(0, 220)}{n.summary.length > 220 ? "…" : ""}</p>)}
          </article>
        ))}
      </div>
    </Card>
  );
}

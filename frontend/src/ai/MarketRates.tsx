import { useEffect, useMemo, useRef, useState } from "react";
import Chart, { axisCommon, baseOption, useTokens } from "../components/Chart";
import { Button, Card, Empty, Grid, MiniButton, Pill } from "../components/ui";
import { useApp, useAsync } from "../state";
import { openAsk } from "./Ask";
import {
  MarketHit, marketRatesApi, OurProduct, PeerSet, RateBook, taka,
} from "./api";
import { clearPageExtra, setPageExtra } from "./pageContext";
import { scenarioLink } from "./Scenario";

const muted: React.CSSProperties = { color: "var(--text-muted)", fontSize: "var(--fs-xs)" };
const field: React.CSSProperties = {
  padding: "8px 10px", border: "1px solid var(--border)", borderRadius: 8,
  background: "var(--surface-1)", color: "var(--text-primary)", font: "inherit",
};
const pct = (v: number | null | undefined) => (v == null ? "—" : `${Number(v).toFixed(2)}%`);
const bp = (v: number | null | undefined) => {
  if (v == null) return "—";
  const b = Math.round(Number(v) * 100);
  return `${b > 0 ? "+" : ""}${b} bp`;
};

const PEERS: [PeerSet, string][] = [
  ["competitors", "My competitors"], ["pcb", "Private"], ["fb", "Foreign"], ["scb", "State-owned"],
  ["islamic", "Islamic"], ["all", "All banks"],
];

/** `#/market?book=deposit&product=fd_1y&bank=THE%20CITY&peers=pcb` */
function readHash() {
  const q = new URLSearchParams(location.hash.split("?")[1] ?? "");
  return {
    book: (q.get("book") === "lending" ? "lending" : "deposit") as RateBook,
    product: q.get("product") ?? undefined, bank: q.get("bank") ?? undefined,
    peers: (q.get("peers") as PeerSet) || "competitors",
  };
}

export default function MarketRates() {
  const init = useMemo(readHash, []);
  const [book, setBook] = useState<RateBook>(init.book);
  const [peers, setPeers] = useState<PeerSet>(init.peers);
  const [product, setProduct] = useState<string | undefined>(init.product);
  const [bank, setBank] = useState<string | undefined>(init.bank);
  const grid = useAsync(() => marketRatesApi.grid(book, peers), [book, peers]);

  // Ask FTP reads what is open here: "this bank", "this rate".
  useEffect(() => {
    const cat = grid.data?.categories.find((c) => c.product === product)?.label;
    const bk = grid.data?.banks.find((b) => b.code === bank)?.name;
    setPageExtra({ market: { book, peers, product, bank },
                   marketLabel: [book === "deposit" ? "deposits" : "loans", cat, bk].filter(Boolean).join(" · ") });
  }, [book, peers, product, bank, grid.data]);
  useEffect(() => () => clearPageExtra(), []);

  const pick = (h: MarketHit) => {
    if (h.type === "bank" && h.code) { setBank(h.code); }
    else if (h.product && h.book) { setBook(h.book); setProduct(h.product); }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <SearchBar onPick={pick} />
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
        <div style={{ display: "flex", gap: 2 }} role="group" aria-label="Book">
          <MiniButton active={book === "deposit"} onClick={() => { setBook("deposit"); setProduct(undefined); }}>Deposits</MiniButton>
          <MiniButton active={book === "lending"} onClick={() => { setBook("lending"); setProduct(undefined); }}>Loans</MiniButton>
        </div>
        <div style={{ display: "flex", gap: 2, flexWrap: "wrap" }} role="group" aria-label="Compare with">
          {PEERS.map(([k, label]) => <MiniButton key={k} active={peers === k} onClick={() => setPeers(k)}>{label}</MiniButton>)}
        </div>
        {grid.data?.month && <span style={muted}>Bangladesh Bank, posted rates for {new Date(`${grid.data.month}T00:00:00`).toLocaleDateString("en-GB", { month: "long", year: "numeric" })} · {grid.data.banks.length} banks</span>}
      </div>
      {grid.error ? <Card><Empty title="Could not load the rates" hint={grid.error} /></Card>
        : !grid.data ? <p style={muted}>Loading…</p>
        : !grid.data.month ? <Card><Empty title="No bank-wise rates yet" hint="Bangladesh Bank's tables are collected with the public data job." /></Card>
        : <>
            {product && <CategoryCard book={book} product={product} peers={peers} onClose={() => setProduct(undefined)}
                                      onBank={setBank} />}
            {bank && <BankCard code={bank} onClose={() => setBank(undefined)} onCategory={(b, p) => { setBook(b); setProduct(p); }} />}
            <GridCard data={grid.data} product={product} onCategory={setProduct} onBank={setBank} />
          </>}
      <OurProducts />
      <Movers peers={peers} />
    </div>
  );
}

// --------------------------------------------------------------------------
// Search
// --------------------------------------------------------------------------

function SearchBar({ onPick }: { onPick: (h: MarketHit) => void }) {
  const { ai } = useApp();
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<MarketHit[]>([]);
  const [open, setOpen] = useState(false);
  const seq = useRef(0);
  useEffect(() => {
    if (q.trim().length < 2) { setHits([]); return; }
    const id = ++seq.current;
    const t = window.setTimeout(() => {
      marketRatesApi.search(q).then((r) => { if (id === seq.current) setHits(r.items); }).catch(() => {});
    }, 150);
    return () => window.clearTimeout(t);
  }, [q]);
  const ask = () => { if (q.trim()) openAsk(q.trim(), undefined, true); };
  return (
    <div style={{ position: "relative", display: "flex", gap: 8, maxWidth: 760 }}>
      <input value={q} onChange={(e) => { setQ(e.target.value); setOpen(true); }}
             onFocus={() => setOpen(true)} onBlur={() => window.setTimeout(() => setOpen(false), 150)}
             onKeyDown={(e) => {
               if (e.key === "Enter") { if (hits[0] && !e.shiftKey) { onPick(hits[0]); setOpen(false); } else ask(); }
             }}
             placeholder="Search a bank (City, EBL, BRAC…), a rate (1 year FD, home loan…) or one of our products"
             aria-label="Search banks and rates" style={{ ...field, flex: 1 }} />
      {ai?.can_chat && <Button onClick={ask} disabled={!q.trim()} icon="sparkle">Ask AI</Button>}
      {open && hits.length > 0 && (
        <div role="listbox" style={{ position: "absolute", top: 42, left: 0, right: ai?.can_chat ? 110 : 0, zIndex: 20,
                                     background: "var(--surface-1)", border: "1px solid var(--border)",
                                     borderRadius: 8, boxShadow: "var(--shadow-md, 0 8px 24px rgba(0,0,0,.12))" }}>
          {hits.map((h, i) => (
            <button key={i} type="button" role="option" aria-selected={false}
                    onMouseDown={(e) => { e.preventDefault(); onPick(h); setOpen(false); }}
                    style={{ all: "unset", cursor: "pointer", display: "flex", gap: 8, width: "calc(100% - 20px)",
                             padding: "8px 10px", borderBottom: "1px solid var(--border)" }}>
              <Pill tone={h.type === "bank" ? "info" : h.type === "category" ? "neutral" : "good"}>{h.type}</Pill>
              <span style={{ fontWeight: 600 }}>{h.label}</span>
              <span style={{ ...muted, marginLeft: "auto" }}>{h.detail}</span>
            </button>))}
          {ai?.can_chat && <button type="button" onMouseDown={(e) => { e.preventDefault(); ask(); }}
                                   style={{ all: "unset", cursor: "pointer", display: "block", padding: "8px 10px",
                                            color: "var(--accent)", fontWeight: 600 }}>
            Ask the AI: “{q}” →</button>}
        </div>)}
    </div>
  );
}

// --------------------------------------------------------------------------
// The grid
// --------------------------------------------------------------------------

function GridCard({ data, product, onCategory, onBank }: {
  data: import("./api").MarketGrid; product?: string;
  onCategory: (p: string) => void; onBank: (c: string) => void;
}) {
  const [onlySet, setOnlySet] = useState(true);
  const banks = data.banks.filter((b) => b.self || !onlySet || b.in_set);
  // Colour each cell by its place in its column: dark = high rate.
  const shade = (p: string, v: number | null) => {
    if (v == null) return "transparent";
    const col = data.banks.map((b) => b.rates[p]).filter((x): x is number => x != null).map(Number).sort((a, b) => a - b);
    const i = col.findIndex((x) => x >= Number(v));
    const q = col.length > 1 ? i / (col.length - 1) : 0.5;
    return `color-mix(in srgb, var(--accent) ${Math.round(6 + q * 34)}%, transparent)`;
  };
  const deposit = data.book === "deposit";
  const th: React.CSSProperties = { position: "sticky", top: 0, background: "var(--surface-1)", padding: "6px 8px",
    fontSize: "var(--fs-xs)", fontWeight: 650, textAlign: "right", borderBottom: "1px solid var(--border)",
    cursor: "pointer", verticalAlign: "bottom", minWidth: 84, zIndex: 1 };
  const td: React.CSSProperties = { padding: "5px 8px", textAlign: "right", fontVariantNumeric: "tabular-nums",
    fontSize: "var(--fs-sm)", borderBottom: "1px solid var(--border)" };
  return (
    <Card title={`Every bank's posted ${deposit ? "deposit" : "lending"} rates`}
          subtitle={`Darker is a higher rate within each column. Click a column for that rate across all banks, a bank for its full card. ${deposit ? "Depositors look for the highest" : "Borrowers look for the lowest"}.`}
          actions={<MiniButton active={onlySet} onClick={() => setOnlySet((v) => !v)}>
            {onlySet ? `Showing ${data.peer_label}` : "Showing all banks"}</MiniButton>}
          footnote="Source: Bangladesh Bank, bank-wise interest rates of scheduled banks. A range is shown at its middle; hover a cell for the bank.">
      <div style={{ overflow: "auto", maxHeight: 560 }}>
        <table style={{ borderCollapse: "separate", borderSpacing: 0, width: "100%" }}>
          <thead><tr>
            <th style={{ ...th, textAlign: "left", left: 0, zIndex: 2, minWidth: 200, cursor: "default" }}>Bank</th>
            {data.categories.map((c) => (
              <th key={c.product} style={{ ...th, color: product === c.product ? "var(--accent)" : undefined }}
                  onClick={() => onCategory(c.product)} title={`Open ${c.label} across all banks`}>{c.label}</th>))}
          </tr></thead>
          <tbody>
            <tr style={{ background: "var(--surface-2)" }}>
              <td style={{ ...td, textAlign: "left", fontWeight: 650, position: "sticky", left: 0, background: "var(--surface-2)" }}>
                Our customers get <span style={muted}>(book)</span></td>
              {data.categories.map((c) => <td key={c.product} style={{ ...td, fontWeight: 650 }}>{pct(c.book?.rate)}</td>)}
            </tr>
            {[["All banks' median", "median"], [`Median, ${data.peer_label}`, "peer_median"]].map(([label, k]) => (
              <tr key={k} style={{ background: "var(--surface-2)" }}>
                <td style={{ ...td, textAlign: "left", position: "sticky", left: 0, background: "var(--surface-2)" }}>{label}</td>
                {data.categories.map((c) => <td key={c.product} style={td}>{pct((c as unknown as Record<string, number | null>)[k])}</td>)}
              </tr>))}
            {banks.map((b) => (
              <tr key={b.code} style={b.self ? { outline: "2px solid var(--accent)", outlineOffset: -2 } : undefined}>
                <td style={{ ...td, textAlign: "left", position: "sticky", left: 0, background: "var(--surface-1)",
                             fontWeight: b.self ? 700 : 500, cursor: "pointer" }} onClick={() => onBank(b.code)}>
                  {b.name}{b.self && <span style={muted}> · us (posted)</span>}
                  {b.islamic && <span style={muted}> · Islamic</span>}</td>
                {data.categories.map((c) => (
                  <td key={c.product} title={`${b.name}: ${c.label} ${pct(b.rates[c.product])}`}
                      style={{ ...td, background: shade(c.product, b.rates[c.product]), fontWeight: b.self ? 700 : 400 }}>
                    {pct(b.rates[c.product])}</td>))}
              </tr>))}
          </tbody>
        </table>
      </div>
      <div style={{ ...muted, marginTop: 6 }}>
        Our rank ({deposit ? "highest-paying first" : "cheapest first"}): {data.categories.filter((c) => c.rank)
          .map((c) => `${c.label} ${c.rank}/${c.banks}`).join(" · ")}
      </div>
    </Card>
  );
}

// --------------------------------------------------------------------------
// One rate type across every bank
// --------------------------------------------------------------------------

function CategoryCard({ book, product, peers, onClose, onBank }: {
  book: RateBook; product: string; peers: PeerSet; onClose: () => void; onBank: (c: string) => void;
}) {
  const t = useTokens();
  const { can } = useApp();
  const c = useAsync(() => marketRatesApi.category(book, product, peers), [book, product, peers]);
  const d = c.data;
  const option = useMemo(() => {
    if (!d?.available) return null;
    const pts = d.banks.map((b, i) => ({ value: [Number(b.mid), (i % 7) - 3], name: b.name, self: b.self, inSet: b.in_set }));
    // Labels at different heights: the medians are often within a few bp.
    const pos = ["insideEndTop", "insideStartTop", "end"] as const;
    const marks = [
      { name: "All banks' median", xAxis: Number(d.standing.median) },
      ...(d.standing.peer_median != null ? [{ name: `${d.peer_label} median`, xAxis: Number(d.standing.peer_median) }] : []),
      ...(d.book_rate?.rate != null ? [{ name: "Our customers get", xAxis: Number(d.book_rate.rate) }] : []),
    ].map((m, i) => ({ ...m, label: { position: pos[i % 3], formatter: "{b}", color: t.textSecondary, fontSize: 10 } }));
    return {
      ...baseOption(t), grid: { left: 8, right: 24, top: 30, bottom: 8, containLabel: true }, legend: { show: false },
      tooltip: { ...baseOption(t).tooltip, trigger: "item" as const,
                 formatter: (p: unknown) => { const x = p as { data: { name: string; value: number[] } };
                   return `<b>${x.data.name}</b><br/>${x.data.value[0].toFixed(2)}%`; } },
      xAxis: { type: "value" as const, scale: true, ...axisCommon(t), axisLabel: { color: t.muted, formatter: "{value}%" } },
      yAxis: { type: "value" as const, show: false, min: -5, max: 5 },
      series: [{
        type: "scatter" as const, symbolSize: (_: unknown, p: unknown) => ((p as { data: { self: boolean } }).data.self ? 18 : 9),
        data: pts,
        itemStyle: { color: (p: unknown) => { const x = (p as { data: { self: boolean; inSet: boolean } }).data;
          return x.self ? t.critical : x.inSet ? t.series[0] : t.muted; }, opacity: 0.85 },
        markArea: d.standing.p25 != null ? { silent: true, itemStyle: { color: t.series[0], opacity: 0.07 },
                    data: [[{ xAxis: Number(d.standing.p25) }, { xAxis: Number(d.standing.p75) }]] } : undefined,
        markLine: { symbol: "none", label: { formatter: "{b}", color: t.textSecondary, fontSize: 10 },
                    lineStyle: { color: t.axis, width: 1 }, data: marks },
      }],
    } as never;
  }, [d, t]);
  if (c.error) return <Card title="Rate type"><Empty title="Could not load" hint={c.error} /></Card>;
  if (!d) return <Card title="Rate type"><p style={muted}>Loading…</p></Card>;
  if (!d.available) return null;
  const s = d.standing;
  const gap = s.self != null && s.peer_median != null ? Number(s.self) - Number(s.peer_median) : null;
  return (
    <Card title={`${d.label} across ${d.banks.length} banks`}
          subtitle={`We rank ${s.rank ?? "—"} of ${s.banks} (${book === "deposit" ? "highest-paying" : "cheapest"} first${s.percentile != null ? `, above ${s.percentile}% of banks` : ""}). The shaded band is the middle half of the market.`}
          actions={<div style={{ display: "flex", gap: 6 }}>
            {can("SCENARIO_RUN") && d.book_rate?.rate != null && s.peer_median != null && d.book_rate.products.length > 0 && (
              <MiniButton onClick={() => { location.hash = scenarioLink({ horizon_months: 6 }); }}
                          title="Open the Scenario lab to test a rate change on these products">Simulate a change</MiniButton>)}
            <MiniButton onClick={onClose}>Close</MiniButton></div>}>
      <Grid cols="repeat(auto-fit, minmax(150px, 1fr))" gap={10}>
        {[["Our posted rate", pct(s.self)], ["Our customers get", pct(d.book_rate?.rate)],
          [`Median, ${d.peer_label}`, pct(s.peer_median)], ["All banks' median", pct(s.median)],
          ["Market range", `${pct(s.min)} – ${pct(s.max)}`], ["Our posted vs peers", bp(gap)]].map(([k, v]) => (
          <div key={k}><div style={muted}>{k}</div><b className="tnum" style={{ fontSize: 18 }}>{v}</b></div>))}
      </Grid>
      {option && <Chart option={option} height={200} ariaLabel={`${d.label}: every bank's posted rate`} />}
      {d.book_rate && d.book_rate.products.length > 0 && <div style={muted}>Our products on this line: {d.book_rate.products.join(", ")}</div>}
      {d.trend.length > 1 && <div style={muted}>Trend: {d.trend.map((x) => `${x.month.slice(0, 7)} median ${pct(x.median)}, us ${pct(x.self)}`).join(" · ")}</div>}
      <details style={{ marginTop: 8 }}>
        <summary style={{ cursor: "pointer", ...muted }}>Every bank, ranked</summary>
        <div style={{ columns: "260px 3", columnGap: 24, marginTop: 6 }}>
          {d.banks.map((b, i) => (
            <div key={b.code} style={{ display: "flex", gap: 8, fontSize: "var(--fs-sm)", padding: "2px 0",
                                       fontWeight: b.self ? 700 : 400, cursor: "pointer" }} onClick={() => onBank(b.code)}>
              <span style={{ width: 22, color: "var(--text-muted)" }}>{i + 1}</span>
              <span style={{ flex: 1 }}>{b.name}</span>
              <span className="tnum">{Number(b.low) === Number(b.high) ? pct(b.mid) : `${pct(b.low)}–${pct(b.high)}`}</span>
            </div>))}
        </div>
      </details>
    </Card>
  );
}

// --------------------------------------------------------------------------
// One bank's card
// --------------------------------------------------------------------------

function BankCard({ code, onClose, onCategory }: { code: string; onClose: () => void; onCategory: (b: RateBook, p: string) => void }) {
  const r = useAsync(() => marketRatesApi.bank(code), [code]);
  const d = r.data;
  if (r.error) return <Card title="Bank"><Empty title="Could not load" hint={r.error} /></Card>;
  if (!d) return <Card title="Bank"><p style={muted}>Loading…</p></Card>;
  const th: React.CSSProperties = { textAlign: "right", padding: "6px 8px", fontSize: "var(--fs-xs)", color: "var(--text-muted)",
    borderBottom: "1px solid var(--border)" };
  const td: React.CSSProperties = { textAlign: "right", padding: "6px 8px", fontSize: "var(--fs-sm)",
    borderBottom: "1px solid var(--border)", fontVariantNumeric: "tabular-nums" };
  return (
    <Card title={d.name} subtitle={`${d.group_label}${d.islamic ? " · Islamic" : ""} · posted rates against ours`}
          actions={<MiniButton onClick={onClose}>Close</MiniButton>}>
      <Grid cols="repeat(auto-fit, minmax(380px, 1fr))" gap={16}>
        {(["deposit", "lending"] as RateBook[]).map((bk) => d.books[bk] && (
          <div key={bk}>
            <div style={{ fontWeight: 650, marginBottom: 4 }}>{bk === "deposit" ? "Deposits" : "Loans"}</div>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead><tr><th style={{ ...th, textAlign: "left" }}>Rate type</th><th style={th}>{d.code === d.self_bank ? "Us" : "Them"}</th>
                <th style={th}>Us (posted)</th><th style={th}>Our book</th><th style={th}>Median</th><th style={th}>They vs us</th></tr></thead>
              <tbody>{d.books[bk]!.rows.map((x) => (
                <tr key={x.product} style={{ cursor: "pointer" }} onClick={() => onCategory(bk, x.product)}>
                  <td style={{ ...td, textAlign: "left" }}>{x.label}</td>
                  <td style={{ ...td, fontWeight: 650 }}>{Number(x.low) === Number(x.high) ? pct(x.mid) : `${pct(x.low)}–${pct(x.high)}`}</td>
                  <td style={td}>{pct(x.our_posted)}</td><td style={td}>{pct(x.our_book)}</td>
                  <td style={td}>{pct(x.median)}</td><td style={td}>{bp(x.gap_to_us)}</td></tr>))}</tbody>
            </table>
          </div>))}
      </Grid>
    </Card>
  );
}

// --------------------------------------------------------------------------
// Our products against the market
// --------------------------------------------------------------------------

function OurProducts() {
  const { can, me, ai } = useApp();
  const [tick, setTick] = useState(0);
  const r = useAsync(() => marketRatesApi.products(), [tick]);
  const [err, setErr] = useState<string | null>(null);
  const editor = can("AI_MARKET_EDIT") && me?.scope_level === "HO";
  const admin = ai?.can_admin;
  const d = r.data;
  if (r.error) return <Card title="Our products against the market"><Empty title="Could not load" hint={r.error} /></Card>;
  if (!d) return <Card title="Our products against the market"><p style={muted}>Loading…</p></Card>;
  const setMap = async (code: string, v: string) => {
    setErr(null);
    try { await marketRatesApi.setProductMap(code, v === "auto" ? null : v); setTick((x) => x + 1); }
    catch (e) { setErr((e as Error).message); }
  };
  const th: React.CSSProperties = { textAlign: "right", padding: "6px 8px", fontSize: "var(--fs-xs)", color: "var(--text-muted)",
    borderBottom: "1px solid var(--border)", textTransform: "uppercase", letterSpacing: ".04em" };
  const td: React.CSSProperties = { textAlign: "right", padding: "7px 8px", fontSize: "var(--fs-sm)",
    borderBottom: "1px solid var(--border)", fontVariantNumeric: "tabular-nums" };
  const rows: (OurProduct | { unmapped: true; product_code: string; name: string; side: string; mapping: string })[] =
    [...d.items, ...d.unmapped.map((u) => ({ ...u, unmapped: true as const }))];
  return (
    <Card title={`Our products against the market${d.label && d.label !== "Whole bank" ? ` · ${d.label}` : ""}`}
          subtitle={`Every active product in the product master — a new one appears here as soon as it is added, a retired one drops out. Each is compared with the nearest line of Bangladesh Bank's table${editor ? "; change it if the guess is wrong" : ""}.`}
          footnote={d.competitors.length ? `Your competitors: ${d.competitors.map((c) => c.name).join(", ")}.`
            : "No competitor list set: comparisons use private banks."}>
      {err && <Pill tone="critical">{err}</Pill>}
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead><tr><th style={{ ...th, textAlign: "left" }}>Product</th><th style={{ ...th, textAlign: "left" }}>Compared with</th>
            <th style={th}>Our customers get</th><th style={th}>Peers' median</th><th style={th}>Market middle half</th>
            <th style={th}>Gap to peers</th><th style={th}>Balance</th></tr></thead>
          <tbody>{rows.map((x) => {
            const um = "unmapped" in x;
            const value = um ? "none" : (x as OurProduct).mapping === "set" ? `${(x as OurProduct).peer_book}:${(x as OurProduct).peer_product}` : "auto";
            const p = x as OurProduct;
            return (
              <tr key={x.product_code}>
                <td style={{ ...td, textAlign: "left" }}><b>{x.name}</b>
                  <div style={muted}>{x.side === "LIABILITY" ? "Deposit" : "Loan"}{!um && p.new && <> · <Pill tone="info">new, no balances yet</Pill></>}</div></td>
                <td style={{ ...td, textAlign: "left" }}>
                  {editor ? (
                    <select value={value} onChange={(e) => setMap(x.product_code, e.target.value)} style={{ ...field, padding: "4px 6px" }}>
                      <option value="auto">Automatic{!um && p.mapping === "auto" ? ` (${p.peer_label})` : ""}</option>
                      {d.categories.filter((c) => c.value.startsWith(x.side === "LIABILITY" ? "deposit" : "lending"))
                        .map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
                      <option value="none">Not compared</option>
                    </select>) : <span style={muted}>{um ? "Not compared" : p.peer_label}</span>}
                </td>
                <td style={{ ...td, fontWeight: 650 }}>{um ? "—" : pct(p.our_rate)}</td>
                <td style={td}>{um ? "—" : pct(p.peer_median ?? p.pcb_median)}</td>
                <td style={{ ...td, color: "var(--text-muted)" }}>{um ? "—" : `${pct(p.p25)} – ${pct(p.p75)}`}</td>
                <td style={{ ...td, fontWeight: 650 }}>{um ? "—" : p.new ? "launch guide" : bp(p.gap_to_peers)}</td>
                <td style={td}>{um || p.new ? "—" : taka(p.balance)}</td>
              </tr>);
          })}</tbody>
        </table>
      </div>
      {admin && <CompetitorEditor current={d.competitors.map((c) => c.code)} banks={d.banks}
                                  onSaved={() => setTick((x) => x + 1)} />}
    </Card>
  );
}

function CompetitorEditor({ current, banks, onSaved }: {
  current: string[]; banks: { code: string; name: string; group: string }[]; onSaved: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [sel, setSel] = useState<string[]>(current);
  const [busy, setBusy] = useState(false);
  useEffect(() => setSel(current), [current]);
  if (!open) return <div style={{ marginTop: 10 }}><MiniButton onClick={() => setOpen(true)}>Choose our competitors…</MiniButton></div>;
  const save = async () => { setBusy(true); try { await marketRatesApi.setCompetitors(sel); onSaved(); setOpen(false); } finally { setBusy(false); } };
  return (
    <div style={{ marginTop: 12, borderTop: "1px solid var(--border)", paddingTop: 10 }}>
      <div style={{ fontWeight: 650, marginBottom: 6 }}>Our competitors ({sel.length})</div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 4, maxHeight: 200, overflow: "auto" }}>
        {banks.map((b) => (
          <MiniButton key={b.code} active={sel.includes(b.code)}
                      onClick={() => setSel((s) => (s.includes(b.code) ? s.filter((x) => x !== b.code) : [...s, b.code]))}>
            {b.name.replace(/ (PLC|Limited|Ltd)\.?$/i, "")}</MiniButton>))}
      </div>
      <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
        <Button variant="primary" onClick={save} disabled={busy}>{busy ? "Saving…" : "Save"}</Button>
        <Button onClick={() => { setSel(current); setOpen(false); }}>Cancel</Button>
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------
// Who moved
// --------------------------------------------------------------------------

function Movers({ peers }: { peers: PeerSet }) {
  const m = useAsync(() => marketRatesApi.movers(peers), [peers]);
  const d = m.data;
  if (!d) return null;
  return (
    <Card title="Who changed their rates" subtitle={`Posted rates that moved 25 bp or more since last month, ${d.peer_label}.`}>
      {!d.available ? <p style={muted}>{d.note}</p> : d.items.length === 0 ? <p style={muted}>No changes of 25 bp or more.</p> : (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {d.items.map((x, i) => (
            <div key={i} style={{ display: "flex", gap: 10, fontSize: "var(--fs-sm)" }}>
              <Pill tone={x.change_bp > 0 ? "warning" : "info"}>{x.change_bp > 0 ? "+" : ""}{x.change_bp} bp</Pill>
              <b>{x.name}</b><span>{x.label}</span><span style={muted}>{pct(x.from)} → {pct(x.to)}</span>
            </div>))}
        </div>)}
    </Card>
  );
}

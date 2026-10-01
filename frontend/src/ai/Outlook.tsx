import { useMemo, useState } from "react";
import { Card, Empty, Grid, MiniButton, Pill, Table } from "../components/ui";
import { compact, longDate, shortDate } from "../format";
import { useApp, useAsync } from "../state";
import Cone, { fmtUnit } from "./Cone";
import { BookMetric, MarketForecast, outlookApi, PeerRow, PolicyOutlook } from "./api";

const muted: React.CSSProperties = { color: "var(--text-muted)", fontSize: "var(--fs-xs)" };
const para: React.CSSProperties = {
  margin: 0, fontSize: "var(--fs-base)", color: "var(--text-secondary)", lineHeight: 1.55,
};

const confTone = (c: string) => (c === "high" ? "good" : c === "medium" ? "info" : "warning") as
  "good" | "info" | "warning";

export function Confidence({ c, why }: { c: string; why: string }) {
  return <span title={why}><Pill tone={confTone(c)}>{c} confidence</Pill></span>;
}

const days = (iso: string | null) =>
  iso ? Math.round((new Date(`${iso}T00:00:00`).getTime() - Date.now()) / 86400000) : null;

export default function Outlook() {
  const { me } = useApp();
  const ho = me?.scope_level === "HO";
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <PolicyCard />
      <BookCard />
      {ho && <PeerCard />}
      <MarketCard />
      <TrackCard />
    </div>
  );
}

// --------------------------------------------------------------------------
// The next policy meeting
// --------------------------------------------------------------------------

function OddsBar({ odds }: { odds: PolicyOutlook["odds"] }) {
  const parts: [keyof typeof odds, string, string][] = [
    ["cut", "Cut", "var(--status-good)"], ["hold", "Hold", "var(--text-muted)"],
    ["hike", "Hike", "var(--status-critical)"]];
  return (
    <div>
      <div style={{ display: "flex", height: 14, borderRadius: 7, overflow: "hidden",
                    border: "1px solid var(--border)" }} role="img"
           aria-label={`Cut ${odds.cut}%, hold ${odds.hold}%, hike ${odds.hike}%`}>
        {parts.map(([k, , c]) => odds[k] > 0 && (
          <div key={k} style={{ width: `${odds[k]}%`, background: c, opacity: k === "hold" ? 0.35 : 0.8 }} />
        ))}
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", marginTop: 6, ...muted }}>
        {parts.map(([k, label]) => <span key={k}><b style={{ color: "var(--text-primary)" }}>
          {odds[k]}%</b> {label}</span>)}
      </div>
    </div>
  );
}

function PolicyCard() {
  const p = useAsync(() => outlookApi.policy(), []);
  if (p.error) return <Card title="Policy rate outlook"><Empty title="Could not load" hint={p.error} /></Card>;
  const d = p.data;
  if (!d) return <Card title="Policy rate outlook"><p style={muted}>Reading the signals…</p></Card>;
  const left = days(d.next_meeting);
  const word = { hike: "a hike", hold: "a hold", cut: "a cut" }[d.leaning];
  const maxPush = Math.max(0.01, ...d.drivers.map((x) => Math.abs(x.push)));
  return (
    <Card title="Bangladesh Bank: the next policy meeting"
          subtitle={d.next_meeting ? `Expected around ${longDate(d.next_meeting)}${left != null && left >= 0 ? ` · in ${left} days` : ""} · ${d.next_meeting_basis}` : d.next_meeting_basis}
          footnote={d.note + (d.missing.length ? ` Not yet available: ${d.missing.join(", ")}.` : "")}>
      <Grid cols="minmax(240px, 1fr) minmax(300px, 2fr)" gap={24}>
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <div>
            <div style={muted}>The signals lean towards</div>
            <div style={{ fontSize: 34, fontWeight: 700, letterSpacing: "-.02em",
                          color: d.leaning === "hike" ? "var(--status-critical)"
                            : d.leaning === "cut" ? "var(--status-good)" : "var(--text-primary)" }}>
              {word[0].toUpperCase() + word.slice(1)}
            </div>
            <div style={muted}>Repo now {d.repo != null ? `${Number(d.repo).toFixed(2)}%` : "—"}
              {d.implied_91d_in_9m != null && <> · T-bill curve implies {Number(d.implied_91d_in_9m).toFixed(2)}% for 3-month money later</>}
            </div>
          </div>
          <OddsBar odds={d.odds} />
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {[...d.drivers].sort((a, b) => Math.abs(b.push) - Math.abs(a.push)).map((x) => (
            <div key={x.key} style={{ display: "grid", gridTemplateColumns: "minmax(140px, 1fr) 120px",
                                      gap: 12, alignItems: "center" }}>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontWeight: 600, fontSize: "var(--fs-sm)" }}>{x.label}</div>
                <div style={muted}>{x.value}</div>
                <div style={{ ...muted, color: "var(--text-secondary)" }}>{x.explain}</div>
              </div>
              <div title={`Push ${x.push > 0 ? "towards a hike" : x.push < 0 ? "towards a cut" : "neutral"}`}
                   style={{ position: "relative", height: 10, background: "var(--surface-2, var(--grid))",
                            borderRadius: 5 }}>
                <div style={{ position: "absolute", left: "50%", top: -3, bottom: -3, width: 1,
                              background: "var(--axis)" }} />
                <div style={{
                  position: "absolute", top: 0, bottom: 0, borderRadius: 5,
                  background: x.push > 0 ? "var(--status-critical)" : "var(--status-good)", opacity: .75,
                  left: x.push >= 0 ? "50%" : `${50 - (Math.abs(x.push) / maxPush) * 50}%`,
                  width: `${(Math.abs(x.push) / maxPush) * 50}%` }} />
              </div>
            </div>
          ))}
          <div style={{ ...muted, display: "flex", justifyContent: "flex-end", gap: 16 }}>
            <span>◀ towards a cut</span><span>towards a hike ▶</span>
          </div>
        </div>
      </Grid>
    </Card>
  );
}

// --------------------------------------------------------------------------
// The book to month-end
// --------------------------------------------------------------------------

function landingText(m: BookMetric) {
  const mo = m.month;
  if (!mo) return null;
  return { mid: fmtUnit(mo.p50, m.unit), lo: fmtUnit(mo.p10, m.unit), hi: fmtUnit(mo.p90, m.unit) };
}

export function BookCard({ branch, title }: { branch?: string; title?: string }) {
  const b = useAsync(() => outlookApi.book(branch), [branch]);
  const [pick, setPick] = useState("net_ftp_profit");
  if (b.error) return <Card title="Where the book is heading"><Empty title="Could not forecast" hint={b.error} /></Card>;
  const d = b.data;
  if (!d) return <Card title="Where the book is heading"><p style={muted}>Forecasting…</p></Card>;
  if (!d.available) return <Card title="Where the book is heading"><Empty title="No forecast yet" hint={d.reason} /></Card>;
  const sel = d.metrics.find((m) => m.metric === pick) ?? d.metrics[0];
  return (
    <Card title={title ?? `Where the book is heading${d.label ? ` · ${d.label}` : ""}`}
          subtitle={`From ${d.days_of_history} days of data to ${longDate(d.latest)}. Month ends ${shortDate(d.month_end)}; quarter ends ${shortDate(d.quarter_end)}.`}
          footnote="Each forecast is a damped trend fitted to the book's own history, with a likely range drawn from how far such forecasts have missed before. Fridays and Saturdays carry Thursday's balances, as the bank's data does.">
      <Grid cols="repeat(auto-fit, minmax(170px, 1fr))" gap={10}>
        {d.metrics.map((m) => {
          const l = landingText(m);
          const active = m.metric === sel?.metric;
          return (
            <button key={m.metric} type="button" onClick={() => setPick(m.metric)}
                    style={{ textAlign: "left", cursor: "pointer", padding: "12px 14px",
                             borderRadius: "var(--radius)", background: "var(--surface-1)",
                             border: `1px solid ${active ? "var(--accent)" : "var(--border)"}`,
                             boxShadow: active ? "0 0 0 3px color-mix(in srgb, var(--accent) 18%, transparent)" : "none",
                             display: "flex", flexDirection: "column", gap: 4, color: "inherit" }}>
              <span style={{ ...muted, fontWeight: 600, textTransform: "uppercase", letterSpacing: ".05em" }}>
                {m.label}{m.kind === "flow" ? ", this month" : " at month-end"}</span>
              <span style={{ fontSize: 22, fontWeight: 700 }} className="tnum">{l?.mid ?? "—"}</span>
              {l && <span style={muted} className="tnum">likely {l.lo} – {l.hi}</span>}
              {m.kind === "flow" && m.month?.so_far != null &&
                <span style={muted}>earned so far {fmtUnit(m.month.so_far, m.unit)}
                  {m.month.previous_month != null && <> · last month {fmtUnit(m.month.previous_month, m.unit)}</>}</span>}
              {m.kind !== "flow" && <span style={muted}>today {fmtUnit(m.last.value, m.unit)}</span>}
              <span><Confidence c={m.confidence} why={m.confidence_reason} /></span>
            </button>
          );
        })}
      </Grid>
      {sel && (
        <div style={{ marginTop: 14 }}>
          <div style={{ ...muted, marginBottom: 4 }}>
            {sel.label}{sel.kind === "flow" ? " per day" : ""} — actual, then forecast with its likely range ·
            {" "}{sel.confidence_reason}{sel.notes.length ? ` · ${sel.notes.join(" ")}` : ""}
          </div>
          <Cone history={sel.history} forecast={sel.forecast} unit={sel.unit} height={260}
                ariaLabel={`${sel.label}: history and forecast`} />
        </div>
      )}
    </Card>
  );
}

// --------------------------------------------------------------------------
// Pricing against the market's posted rates
// --------------------------------------------------------------------------

function PeerCard() {
  const r = useAsync(() => outlookApi.bookVsPeers(), []);
  const rows = useMemo(() => [...(r.data?.items ?? [])].sort((a, b) =>
    Math.abs(b.gap) * b.balance - Math.abs(a.gap) * a.balance), [r.data]);
  if (r.error) return <Card title="Our pricing against the market"><Empty title="Could not load" hint={r.error} /></Card>;
  if (!r.data) return <Card title="Our pricing against the market"><p style={muted}>Loading…</p></Card>;
  const month = rows.find((x) => x.month)?.month;
  const verdict = (x: PeerRow) => {
    const ref = Number(x.pcb_median ?? x.market_median);
    const gap = Number(x.our_rate) - ref;
    if (x.side === "LIABILITY") {
      if (x.p25 != null && Number(x.our_rate) < Number(x.p25)) return { t: "Below most banks: depositors may leave", tone: "critical" as const };
      if (gap <= -0.75) return { t: "Under the private banks", tone: "warning" as const };
      if (gap >= 0.75) return { t: "Paying above the market", tone: "info" as const };
      return { t: "In line", tone: "good" as const };
    }
    if (gap >= 1) return { t: "Above the market: borrowers may refinance", tone: "warning" as const };
    if (gap <= -1) return { t: "Under the market: income left on the table", tone: "warning" as const };
    return { t: "In line", tone: "good" as const };
  };
  return (
    <Card title="Our pricing against the market"
          subtitle={`What our customers actually get (from the book) against every bank's posted rate for the like product${month ? `, ${new Date(`${month}T00:00:00`).toLocaleDateString("en-GB", { month: "long", year: "numeric" })}` : ""}. Private commercial banks are the closest competitors.`}
          footnote="Source: Bangladesh Bank, bank-wise deposit and lending rates. Each product is matched to the table's nearest line by its name and term — an inference, shown in the 'Compared with' column.">
      <Table<PeerRow> rows={rows} csvName="pricing-vs-market" cols={[
        { key: "name", label: "Product", render: (x) => <div><div style={{ fontWeight: 600 }}>{x.name}</div>
          <div style={muted}>{x.side === "LIABILITY" ? "Deposit" : "Loan"} · ৳{compact(x.balance)}</div></div> },
        { key: "peer", label: "Compared with", render: (x) => <span style={muted}>{x.peer_label}</span> },
        { key: "ours", label: "Ours", align: "right", render: (x) => <b className="tnum">{Number(x.our_rate).toFixed(2)}%</b> },
        { key: "pcb", label: "Private banks", align: "right", render: (x) =>
          <span className="tnum">{x.pcb_median != null ? `${Number(x.pcb_median).toFixed(2)}%` : "—"}</span> },
        { key: "range", label: "Market middle half", align: "right", render: (x) =>
          <span className="tnum" style={muted}>{x.p25 != null ? `${Number(x.p25).toFixed(2)}–${Number(x.p75).toFixed(2)}%` : "—"}</span> },
        { key: "gap", label: "Gap", align: "right", render: (x) => {
          const g = Number(x.our_rate) - Number(x.pcb_median ?? x.market_median);
          return <span className="tnum" style={{ fontWeight: 600 }}>{g > 0 ? "+" : ""}{Math.round(g * 100)} bp</span>; } },
        { key: "verdict", label: "", render: (x) => { const v = verdict(x); return <Pill tone={v.tone}>{v.t}</Pill>; } },
      ]} />
    </Card>
  );
}

// --------------------------------------------------------------------------
// Market rates, 90 days on
// --------------------------------------------------------------------------

function MarketTile({ s }: { s: MarketForecast }) {
  const end = s.in_90d;
  const move = end ? Number(end.p50) - Number(s.last.value) : 0;
  const moveText = s.unit === "pct" ? `${move > 0 ? "+" : ""}${Math.round(move * 100)} bp`
    : `${move > 0 ? "+" : ""}${((move / Number(s.last.value)) * 100).toFixed(1)}%`;
  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: "var(--radius)", padding: 12,
                  background: "var(--surface-1)", minWidth: 0 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "baseline" }}>
        <span style={{ fontWeight: 650 }}>{s.short}</span>
        <Confidence c={s.confidence} why={s.confidence_reason} />
      </div>
      <div style={{ display: "flex", gap: 10, alignItems: "baseline", marginTop: 4 }} className="tnum">
        <span style={{ fontSize: 18, fontWeight: 700 }}>{fmtUnit(s.last.value, s.unit)}</span>
        <span style={muted}>→ {end ? fmtUnit(end.p50, s.unit) : "—"} in 90 days ({moveText})</span>
      </div>
      {end && <div style={muted} className="tnum">likely {fmtUnit(end.p10, s.unit)} – {fmtUnit(end.p90, s.unit)}
        {s.bounded_by ? ` · kept within ${s.bounded_by}` : ""}</div>}
      <Cone history={s.history.slice(-60)} forecast={s.forecast} unit={s.unit} height={120} compactAxis
            ariaLabel={`${s.name}: recent values and 90-day forecast`} />
    </div>
  );
}

function MarketCard() {
  const m = useAsync(() => outlookApi.market(), []);
  if (m.error) return <Card title="Market rates, 90 days on"><Empty title="Could not load" hint={m.error} /></Card>;
  if (!m.data) return <Card title="Market rates, 90 days on"><p style={muted}>Forecasting…</p></Card>;
  return (
    <Card title="Market rates, 90 days on"
          subtitle="Each series forecast at its own pace — daily rates by the trading day, monthly averages by the month — with the range its past forecasts would have needed."
          footnote="Sources: Bangladesh Bank (call money since 2016, industry averages), FRED (US rates, Brent), ExchangeRate-API. A forecast is a projection of the series' own path; it does not know about announcements to come.">
      <Grid cols="repeat(auto-fit, minmax(260px, 1fr))" gap={12}>
        {m.data.series.map((s) => <MarketTile key={s.code} s={s} />)}
      </Grid>
    </Card>
  );
}

// --------------------------------------------------------------------------
// The record
// --------------------------------------------------------------------------

function TrackCard() {
  const [tick, setTick] = useState(0);
  const r = useAsync(() => outlookApi.trackRecord(), [tick]);
  const s = r.data?.summary;
  return (
    <Card title="Track record" actions={<MiniButton onClick={() => setTick((x) => x + 1)}>Refresh</MiniButton>}
          subtitle="Every forecast is kept and scored when the outcome is known. A good forecaster's likely range holds about 8 outcomes in 10.">
      {!s ? <p style={muted}>Loading…</p> : s.scored === 0
        ? <p style={para}>{s.pending} forecasts are waiting for their dates to pass. Until then, each forecast above
            shows its backtest: how it would have done on the past, refitted at earlier dates.</p>
        : <p style={para}><b>{s.inside_band} of {s.scored}</b> outcomes fell inside the likely range
            ({Math.round((s.hit_rate ?? 0) * 100)}%). {s.pending} more are pending.</p>}
    </Card>
  );
}

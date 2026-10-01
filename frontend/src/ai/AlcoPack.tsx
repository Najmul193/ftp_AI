import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { Button, Pill } from "../components/ui";
import { longDate } from "../format";
import { useApp } from "../state";
import { Narrative } from "./Brief";
import { fmtUnit } from "./Cone";
import { AlcoPack as Pack, alcoApi, taka } from "./api";

const th: React.CSSProperties = { textAlign: "left", padding: "6px 8px", borderBottom: "1px solid var(--border)",
  fontSize: "var(--fs-xs)", textTransform: "uppercase", letterSpacing: ".05em", color: "var(--text-muted)" };
const td: React.CSSProperties = { padding: "6px 8px", borderBottom: "1px solid var(--border)",
  fontSize: "var(--fs-sm)" };
const num: React.CSSProperties = { ...td, textAlign: "right", fontVariantNumeric: "tabular-nums" };
const h2: React.CSSProperties = { fontSize: "var(--fs-md)", margin: "26px 0 8px", fontWeight: 700,
  borderBottom: "2px solid var(--text-primary)", paddingBottom: 4 };
const muted: React.CSSProperties = { color: "var(--text-muted)", fontSize: "var(--fs-xs)" };

const signed = (v: number | null | undefined) => {
  if (v == null) return "—";
  const x = Number(v);
  return `${x > 0 ? "+" : x < 0 ? "-" : ""}${taka(Math.abs(x))}`;
};

/** On screen the pack opens as a full-screen preview; printed (Download
 *  PDF), only the document shows -- the app behind it and the preview's own
 *  toolbar are taken out of the layout, so no blank pages print. */
const PRINT = `@media print {
  @page { size: A4; margin: 14mm; }
  html, body { background: #fff !important; overflow: visible !important; }
  body > *:not(#alco-pack-host) { display: none !important; }
  #alco-pack-host { position: static !important; background: #fff !important; overflow: visible !important;
                    padding: 0 !important; }
  #alco-pack-host .no-print { display: none !important; }
  #alco-pack-host .alco-sheet { box-shadow: none !important; }
  #alco-pack-host > div { padding: 0 !important; }
  #alco-pack { width: auto !important; padding: 0 !important; box-shadow: none !important; margin: 0 !important; }
  #alco-pack section { break-inside: avoid; }
}`;

type Note = Awaited<ReturnType<typeof alcoApi.commentary>>;

/** The pack as a document: figures from code, an optional AI commentary. */
export function AlcoDocument({ d, note, noteError }: { d: Pack; note: Note | null; noteError: string | null }) {
  const month = new Date(`${d.prepared}T00:00:00`).toLocaleDateString("en-GB", { month: "long", year: "numeric" });
  const pol = d.policy;
  return (
    <div id="alco-pack" style={{ background: "#fff", color: "#111", padding: "28px 34px", width: 1000 }}>
      <style>{PRINT}</style>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 16 }}>
        <div>
          <div style={muted}>Asset-Liability Committee</div>
          <h1 style={{ margin: "2px 0", fontSize: 26 }}>ALCO pack · {month}</h1>
          <div style={muted}>{d.label} · prepared {longDate(d.prepared)} · figures from the FTP platform
            {d.book ? `, book data to ${longDate(d.book.latest)}` : ""}</div>
        </div>
      </div>

      {(note || noteError) && (
        <section>
          <h2 style={h2}>Commentary</h2>
          {noteError && <Pill tone="critical">The commentary could not be written: {noteError}</Pill>}
          {note && <>
            <Narrative text={note.text} />
            <div style={{ ...muted, marginTop: 6 }}>
              {note.grounded ? "Every figure checked against the pack." : note.unverified.length
                ? `Not found in the pack: ${note.unverified.join(", ")}` : ""} · written by {note.provider} ({note.model})
            </div>
          </>}
        </section>)}

      <section>
        <h2 style={h2}>1. Policy rate outlook</h2>
        <p style={{ margin: "0 0 8px" }}>
          Next MPC meeting expected around <b>{pol.next_meeting ? longDate(pol.next_meeting) : "—"}</b>.
          Repo {pol.repo != null ? `${Number(pol.repo).toFixed(2)}%` : "—"}. The signals lean towards
          a <b>{pol.leaning}</b> (cut {pol.odds.cut}% · hold {pol.odds.hold}% · hike {pol.odds.hike}%;
          a summary of the signals, not a market price).</p>
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead><tr><th style={th}>Signal</th><th style={th}>Reading</th><th style={{ ...th, textAlign: "right" }}>Push</th></tr></thead>
          <tbody>{[...pol.drivers].sort((a, b) => Math.abs(b.push) - Math.abs(a.push)).map((x) => (
            <tr key={x.key}><td style={td}><b>{x.label}</b><div style={muted}>{x.explain}</div></td>
              <td style={td}>{x.value}</td>
              <td style={num}>{x.push > 0 ? "hike" : x.push < 0 ? "cut" : "—"} {Math.abs(x.push).toFixed(2)}</td></tr>))}</tbody>
        </table>
      </section>

      <section>
        <h2 style={h2}>2. Market rates, 90 days on</h2>
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead><tr><th style={th}>Series</th><th style={{ ...th, textAlign: "right" }}>Latest</th>
            <th style={{ ...th, textAlign: "right" }}>In 30 days</th><th style={{ ...th, textAlign: "right" }}>In 90 days</th>
            <th style={{ ...th, textAlign: "right" }}>Likely range (90d)</th><th style={th}>Confidence</th></tr></thead>
          <tbody>{d.market.map((s) => (
            <tr key={s.code}><td style={td}>{s.name}</td><td style={num}>{fmtUnit(s.last.value, s.unit)}</td>
              <td style={num}>{fmtUnit(s.in_30d?.p50, s.unit)}</td><td style={num}>{fmtUnit(s.in_90d?.p50, s.unit)}</td>
              <td style={num}>{fmtUnit(s.in_90d?.p10, s.unit)} – {fmtUnit(s.in_90d?.p90, s.unit)}</td>
              <td style={td}>{s.confidence}</td></tr>))}</tbody>
        </table>
      </section>

      {d.book && (
        <section>
          <h2 style={h2}>3. Where the book lands</h2>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead><tr><th style={th}>Measure</th><th style={{ ...th, textAlign: "right" }}>Today / so far</th>
              <th style={{ ...th, textAlign: "right" }}>Month-end, likely</th><th style={{ ...th, textAlign: "right" }}>Range</th>
              <th style={{ ...th, textAlign: "right" }}>Quarter-end, likely</th><th style={th}>Confidence</th></tr></thead>
            <tbody>{d.book.metrics.map((m) => (
              <tr key={m.metric}><td style={td}>{m.label}{m.kind === "flow" ? " (the period's total)" : ""}</td>
                <td style={num}>{fmtUnit(m.kind === "flow" ? m.month?.so_far : m.last.value, m.unit)}</td>
                <td style={num}>{fmtUnit(m.month?.p50, m.unit)}</td>
                <td style={num}>{fmtUnit(m.month?.p10, m.unit)} – {fmtUnit(m.month?.p90, m.unit)}</td>
                <td style={num}>{fmtUnit(m.quarter?.p50, m.unit)}</td><td style={td}>{m.confidence}</td></tr>))}</tbody>
          </table>
        </section>)}

      {d.sensitivity.length > 0 && (
        <section>
          <h2 style={h2}>4. NII sensitivity</h2>
          <p style={{ ...muted, margin: "0 0 6px" }}>Change a month, three months in, with the bank's usual pass-through
            (term deposits 50%, savings & current 20%, loans 70%; FTP benchmarks follow the market).</p>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead><tr><th style={th}>Move</th><th style={{ ...th, textAlign: "right" }}>Bank NII</th>
              <th style={{ ...th, textAlign: "right" }}>Branches' FTP profit</th><th style={{ ...th, textAlign: "right" }}>Treasury</th>
              <th style={{ ...th, textAlign: "right" }}>Deposits</th><th style={{ ...th, textAlign: "right" }}>NIM</th></tr></thead>
            <tbody>{d.sensitivity.map((r) => (
              <tr key={r.label}><td style={td}>{r.label}</td><td style={num}>{signed(r.bank_nii)}</td>
                <td style={num}>{signed(r.branch_ftp)}</td><td style={num}>{signed(r.treasury)}</td>
                <td style={num}>{signed(r.deposits)}</td>
                <td style={num}>{r.nim != null ? `${Number(r.nim) > 0 ? "+" : ""}${Math.round(Number(r.nim) * 100)} bp` : "—"}</td></tr>))}</tbody>
          </table>
        </section>)}

      {d.pricing.length > 0 && (
        <section>
          <h2 style={h2}>5. Our pricing against other banks</h2>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead><tr><th style={th}>Product</th><th style={{ ...th, textAlign: "right" }}>Ours</th>
              <th style={{ ...th, textAlign: "right" }}>Private banks</th><th style={{ ...th, textAlign: "right" }}>Gap</th>
              <th style={{ ...th, textAlign: "right" }}>Balance</th></tr></thead>
            <tbody>{d.pricing.map((x) => {
              const g = Number(x.our_rate) - Number(x.pcb_median ?? x.market_median);
              return (<tr key={x.product_code}><td style={td}>{x.name}<div style={muted}>{x.peer_label}</div></td>
                <td style={num}>{Number(x.our_rate).toFixed(2)}%</td>
                <td style={num}>{x.pcb_median != null ? `${Number(x.pcb_median).toFixed(2)}%` : "—"}</td>
                <td style={{ ...num, fontWeight: Math.abs(g) >= 0.75 ? 700 : 400 }}>{g > 0 ? "+" : ""}{Math.round(g * 100)} bp</td>
                <td style={num}>{taka(x.balance)}</td></tr>);
            })}</tbody>
          </table>
          <div style={{ ...muted, marginTop: 4 }}>Source: Bangladesh Bank bank-wise posted rates. Products matched to the nearest line by name and term.</div>
        </section>)}

      {d.benchmarks.length > 0 && (
        <section>
          <h2 style={h2}>6. FTP benchmarks against the curve</h2>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead><tr><th style={th}>Product</th><th style={{ ...th, textAlign: "right" }}>Benchmark</th>
              <th style={{ ...th, textAlign: "right" }}>Market at its term</th><th style={{ ...th, textAlign: "right" }}>Gap</th>
              <th style={{ ...th, textAlign: "right" }}>Effect a month</th></tr></thead>
            <tbody>{[...d.benchmarks].sort((a, b) => Math.abs(b.gap_bp ?? 0) - Math.abs(a.gap_bp ?? 0)).map((b) => (
              <tr key={b.product_code}><td style={td}>{b.name}{b.behavioural ? <span style={muted}> · behavioural</span> : null}</td>
                <td style={num}>{b.benchmark != null ? `${Number(b.benchmark).toFixed(2)}%` : "—"}</td>
                <td style={num}>{b.market != null ? `${Number(b.market).toFixed(2)}%` : "—"}</td>
                <td style={num}>{b.gap_bp != null ? `${b.gap_bp > 0 ? "+" : ""}${b.gap_bp} bp` : "—"}</td>
                <td style={num}>{signed(b.monthly_impact)}</td></tr>))}</tbody>
          </table>
        </section>)}

      <section>
        <h2 style={h2}>7. For decision</h2>
        {d.decisions.length === 0 ? <p style={muted}>No open findings need a decision.</p> : (
          <ol style={{ margin: 0, paddingLeft: 20 }}>
            {d.decisions.map((x) => (
              <li key={x.id} style={{ marginBottom: 6 }}>
                <b>{x.title}</b>{x.money != null && <span style={muted}> — {taka(x.money)} {x.basis ?? ""}</span>}
              </li>))}
          </ol>)}
      </section>

      <section>
        <h2 style={h2}>Annex: the economy</h2>
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead><tr><th style={th}>Indicator</th><th style={{ ...th, textAlign: "right" }}>Latest outturn</th>
            <th style={{ ...th, textAlign: "right" }}>IMF projection</th></tr></thead>
          <tbody>{d.macro.filter((m) => m.actual.length || m.projection.length).map((m) => {
            const a = m.actual[m.actual.length - 1];
            const pr = m.projection[0];
            const f = (v: number) => (m.unit === "usd_bn" ? `$${Number(v).toFixed(1)} bn` : `${Number(v).toFixed(1)}%`);
            return (<tr key={m.code}><td style={td}>{m.name}</td>
              <td style={num}>{a ? `${f(a.value)} (${a.year})` : "—"}</td>
              <td style={num}>{pr ? `${f(pr.value)} (${pr.year})` : "—"}</td></tr>);
          })}</tbody>
        </table>
        <div style={{ ...muted, marginTop: 4 }}>Sources: World Bank; IMF World Economic Outlook.</div>
      </section>
    </div>
  );
}


/**
 * "ALCO pack" on the Intelligence page: assembles the month's pack and opens
 * it as a full-screen preview, where the AI commentary can be added (every
 * number checked against the pack) and the PDF downloaded. No page of its own.
 */
export function AlcoDownload() {
  const { ai, can } = useApp();
  const [open, setOpen] = useState(false);
  const [d, setD] = useState<Pack | null>(null);
  const [note, setNote] = useState<Note | null>(null);
  const [noteError, setNoteError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [writing, setWriting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const show = async () => {
    setOpen(true); setErr(null); setLoading(true);
    try { setD(await alcoApi.get()); } catch (e) { setErr((e as Error).message); } finally { setLoading(false); }
  };
  const close = () => { setOpen(false); setD(null); setNote(null); setNoteError(null); };
  const write = async () => {
    setWriting(true); setNoteError(null);
    try { setNote(await alcoApi.commentary()); } catch (e) { setNoteError((e as Error).message); }
    finally { setWriting(false); }
  };
  const download = () => {
    if (!d) return;
    const month = new Date(`${d.prepared}T00:00:00`).toLocaleDateString("en-GB", { month: "long", year: "numeric" });
    const title = document.title;
    // The saved file takes the page title as its name.
    document.title = `ALCO pack - ${month}`;
    window.addEventListener("afterprint", () => { document.title = title; }, { once: true });
    window.print();
  };

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") close(); };
    document.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.removeEventListener("keydown", onKey); document.body.style.overflow = prev; };
  }, [open]);

  if (!can("SCENARIO_RUN")) return null;
  return (
    <>
      <Button variant="primary" icon="download" onClick={show}>ALCO pack</Button>
      {open && createPortal(
        <div id="alco-pack-host" role="dialog" aria-modal="true" aria-label="ALCO pack preview"
             style={{ position: "fixed", inset: 0, zIndex: 200, overflow: "auto",
                      background: "color-mix(in srgb, var(--scrim, #0b1220) 70%, transparent)" }}>
          <div className="no-print" style={{ position: "sticky", top: 0, zIndex: 1, display: "flex", gap: 8,
                                             alignItems: "center", padding: "10px 18px",
                                             background: "var(--surface-1)", borderBottom: "1px solid var(--border)" }}>
            <b style={{ flex: 1 }}>ALCO pack · preview</b>
            {ai?.can_chat && d && (
              <Button onClick={write} disabled={writing}>
                {writing ? "Writing the commentary…" : note ? "Rewrite AI commentary" : "Add AI commentary"}</Button>)}
            <Button variant="primary" icon="download" onClick={download} disabled={!d || writing}>Download PDF</Button>
            <Button onClick={close}>Close</Button>
          </div>
          <div style={{ padding: "24px 0 48px", display: "flex", justifyContent: "center" }}>
            {loading && <p className="no-print" style={{ color: "#fff" }}>Preparing the pack…</p>}
            {err && <Pill tone="critical">{err}</Pill>}
            {d && <div className="alco-sheet" style={{ boxShadow: "0 12px 40px rgba(0,0,0,.35)" }}>
              <AlcoDocument d={d} note={note} noteError={noteError} /></div>}
          </div>
        </div>,
        document.body)}
    </>
  );
}

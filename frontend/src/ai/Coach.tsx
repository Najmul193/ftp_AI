import { useEffect, useState } from "react";
import { Button, Card, Empty, Grid, MiniButton, Pill } from "../components/ui";
import { shortDate } from "../format";
import { useApp, useAsync } from "../state";
import { coachApi, CoachMetric, CoachNote, taka } from "./api";
import { openAsk } from "./Ask";
import { Narrative } from "./Brief";
import { clearPageExtra, setPageExtra } from "./pageContext";

const para: React.CSSProperties = {
  margin: 0, fontSize: "var(--fs-base)", color: "var(--text-secondary)", lineHeight: 1.55,
};
const LAST = "ftp_coach_branch";

function fmt(v: number | null, unit: CoachMetric["unit"]) {
  if (v == null) return "—";
  if (unit === "bdt") return taka(v);
  if (unit === "share") return `${Number(v).toFixed(1)}%`;
  return `${Number(v).toFixed(2)}%`;
}

export default function Coach() {
  const { ai } = useApp();
  const list = useAsync(() => coachApi.branches(), []);
  const branches = list.data?.items ?? [];
  const [code, setCode] = useState<string | null>(null);

  useEffect(() => {
    if (!branches.length || code) return;
    let last: string | null = null;
    try { last = localStorage.getItem(LAST); } catch { /* private mode */ }
    setCode(branches.some((b) => b.code === last) ? last : branches[0].code);
  }, [branches, code]);
  useEffect(() => { if (code) try { localStorage.setItem(LAST, code); } catch { /* private mode */ } }, [code]);
  // Ask FTP asked here means this branch.
  useEffect(() => {
    const b = branches.find((x) => x.code === code);
    if (b) setPageExtra({ branch: b.code, branchLabel: `${b.name} (${b.code})` });
    return () => clearPageExtra();
  }, [code, branches]);

  if (list.error) return <Card><Empty title="Could not load branches" hint={list.error} /></Card>;
  if (list.data && !branches.length)
    return <Card><Empty title="No branch to coach" hint="Your part of the bank has no active branch." /></Card>;
  if (!code) return <p style={{ color: "var(--text-muted)" }}>Loading…</p>;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      {branches.length > 1 && (
        <label style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <span style={{ fontSize: "var(--fs-sm)", color: "var(--text-muted)", fontWeight: 600 }}>Branch</span>
          <select value={code} onChange={(e) => setCode(e.target.value)} style={{ minWidth: 260 }}>
            {branches.map((b) => (
              <option key={b.code} value={b.code}>{b.name} ({b.code}) · {b.district}</option>))}
          </select>
        </label>)}
      <BranchView code={code} canNote={Boolean(ai?.provider)} canAsk={Boolean(ai?.can_chat)} />
    </div>
  );
}

function BranchView({ code, canNote, canAsk }: { code: string; canNote: boolean; canAsk: boolean }) {
  const v = useAsync(() => coachApi.get(code), [code]);
  if (v.error) return <Card><Empty title="No coaching for this branch" hint={v.error} /></Card>;
  const d = v.data;
  if (!d) return <p style={{ color: "var(--text-muted)" }}>Loading…</p>;

  const r = d.rank;
  const moved = r.profit && r.profit_before ? r.profit_before - r.profit[0] : 0;
  const total = d.actions.reduce((s, a) => s + (a.basis === "per month" && a.money ? Number(a.money) : 0), 0);

  return (
    <>
      <Card title={`${d.branch.name} (${d.branch.code})`}
            subtitle={`Week of ${shortDate(d.period.start)}–${shortDate(d.period.end)} against the week before · `
                      + `compared with the ${d.peers.count} branches of ${d.peers.name}`}
            actions={canAsk && (
              <MiniButton icon="sparkle" onClick={() => openAsk(
                `How did branch ${d.branch.name} do this month, and what changed?`)}>
                Ask about this branch</MiniButton>)}>
        <Grid cols="repeat(auto-fit, minmax(200px, 1fr))" gap={12}>
          <RankTile label="FTP profit rank" rank={r.profit} moved={moved} />
          <RankTile label="FTP yield rank" rank={r.yield} />
          <div style={{ padding: "14px 16px", border: "1px solid var(--border)", borderRadius: "var(--radius)" }}>
            <div style={{ fontSize: "var(--fs-xs)", fontWeight: 600, letterSpacing: ".05em",
                          textTransform: "uppercase", color: "var(--text-secondary)" }}>Worth acting on</div>
            <div style={{ fontSize: "var(--fs-xl)", fontWeight: 650, marginTop: 4 }}>
              {total ? taka(total) : "—"}</div>
            <div style={{ fontSize: "var(--fs-sm)", color: "var(--text-muted)" }}>
              {total ? "a month, across the actions below" : "nothing below the peer median"}</div>
          </div>
        </Grid>
      </Card>

      <Card title="Do this week"
            subtitle="The largest money first. Each is what reaching the peer median would be worth, on this branch's own balances">
        {d.actions.length === 0
          ? <p style={para}><Pill tone="good">On track</Pill> This branch is at or better than its peer median on deposit cost, CASA share and loan pricing. Nothing to fix this week.</p>
          : <ol style={{ margin: 0, padding: 0, listStyle: "none" }}>
              {d.actions.map((a, i) => (
                <li key={a.key} style={{ display: "flex", gap: 12, padding: "12px 0",
                                         borderTop: i ? "1px solid var(--border)" : undefined }}>
                  <span className="tnum" aria-hidden style={{
                    width: 26, height: 26, borderRadius: 99, flexShrink: 0, display: "grid", placeItems: "center",
                    background: "var(--accent-soft)", color: "var(--accent)", fontWeight: 700,
                    fontSize: "var(--fs-sm)" }}>{i + 1}</span>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: "flex", gap: 10, alignItems: "baseline", flexWrap: "wrap" }}>
                      <b style={{ fontSize: "var(--fs-md)", color: "var(--text-primary)" }}>{a.title}</b>
                      {a.money != null && (
                        <span className="tnum" style={{ fontWeight: 650, color: "var(--text-primary)" }}>
                          {taka(a.money)} <span style={{ fontWeight: 500, color: "var(--text-muted)" }}>{a.basis}</span>
                        </span>)}
                    </div>
                    <p style={{ ...para, marginTop: 3 }}>{a.body}</p>
                  </div>
                </li>))}
            </ol>}
        {d.strengths.length > 0 && (
          <div style={{ marginTop: 12, paddingTop: 12, borderTop: "1px solid var(--border)" }}>
            <div style={{ fontSize: "var(--fs-xs)", fontWeight: 700, letterSpacing: ".06em",
                          textTransform: "uppercase", color: "var(--text-muted)", marginBottom: 4 }}>Going well</div>
            {d.strengths.map((s) => <p key={s} style={para}><span aria-hidden style={{ color: "var(--status-good)" }}>✓ </span>{s}</p>)}
          </div>)}
      </Card>

      <Card title="Against the peers"
            subtitle={`Each measure for the week against the median of ${d.peers.name}; rank is among those ${d.peers.count} branches`}
            footnote="Rates are annualised. Deposits and advances are average daily balances. Only the peers' median and this branch's rank are shown, never another branch's figures.">
        <Grid cols="repeat(auto-fill, minmax(220px, 1fr))" gap={12}>
          {d.metrics.map((m) => <MetricTile key={m.key} m={m} />)}
        </Grid>
      </Card>

      {canNote && <CoachNoteCard code={code} />}
    </>
  );
}

function RankTile({ label, rank, moved = 0 }: { label: string; rank: [number, number] | null; moved?: number }) {
  return (
    <div style={{ padding: "14px 16px", border: "1px solid var(--border)", borderRadius: "var(--radius)" }}>
      <div style={{ fontSize: "var(--fs-xs)", fontWeight: 600, letterSpacing: ".05em",
                    textTransform: "uppercase", color: "var(--text-secondary)" }}>{label}</div>
      <div style={{ fontSize: "var(--fs-xl)", fontWeight: 650, marginTop: 4 }}>
        {rank ? <>#{rank[0]} <span style={{ fontSize: "var(--fs-md)", fontWeight: 500, color: "var(--text-muted)" }}>of {rank[1]}</span></> : "—"}
      </div>
      <div style={{ fontSize: "var(--fs-sm)", color: "var(--text-muted)" }}>
        {moved > 0 ? `▲ up ${moved} on last week` : moved < 0 ? `▼ down ${-moved} on last week` : "among all branches"}
      </div>
    </div>
  );
}

function MetricTile({ m }: { m: CoachMetric }) {
  const tone = m.better == null ? "neutral" : m.better ? "good" : "warning";
  return (
    <div style={{ padding: "12px 14px", border: "1px solid var(--border)", borderRadius: "var(--radius-sm)",
                  display: "flex", flexDirection: "column", gap: 4 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center" }}>
        <span style={{ fontSize: "var(--fs-sm)", fontWeight: 600, color: "var(--text-secondary)" }}>{m.label}</span>
        {m.better != null && <Pill tone={tone}>{m.better ? "Ahead of peers" : "Behind peers"}</Pill>}
      </div>
      <div className="tnum" style={{ fontSize: "var(--fs-lg)", fontWeight: 650, color: "var(--text-primary)" }}>
        {fmt(m.value, m.unit)}</div>
      <div className="tnum" style={{ fontSize: "var(--fs-sm)", color: "var(--text-muted)" }}>
        Peer median {fmt(m.district_median, m.unit)}
        {m.district_rank && <> · #{m.district_rank[0]} of {m.district_rank[1]}</>}
      </div>
    </div>
  );
}

function CoachNoteCard({ code }: { code: string }) {
  const [lang, setLang] = useState<"en" | "bn">("en");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<CoachNote | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { setNote(null); setErr(null); }, [code, lang]);

  const write = async () => {
    setBusy(true); setErr(null);
    try { setNote(await coachApi.note(code, lang)); }
    catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  };

  return (
    <Card title="Coaching note"
          subtitle="The figures above, told to the branch manager in plain words by the AI provider"
          actions={<div style={{ display: "flex", gap: 2 }}>
            <MiniButton active={lang === "en"} onClick={() => setLang("en")}>EN</MiniButton>
            <MiniButton active={lang === "bn"} onClick={() => setLang("bn")}>বাংলা</MiniButton>
          </div>}>
      {!note && (
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <Button icon="sparkle" disabled={busy} onClick={write}>
            {busy ? "Writing…" : lang === "bn" ? "বাংলায় লিখুন" : "Write a coaching note"}</Button>
          <span style={{ fontSize: "var(--fs-sm)", color: "var(--text-muted)" }}>
            Sent through the privacy gateway: the branch travels as a token, amounts are blurred, every figure is checked.
          </span>
        </div>)}
      {err && <p style={para}><Pill tone="critical">Not written</Pill> {err}</p>}
      {note && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <Narrative text={note.text} />
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center",
                        fontSize: "var(--fs-sm)", color: "var(--text-muted)" }}>
            {note.grounded ? <Pill tone="good">Numbers checked against the facts</Pill>
              : note.grounded === false ? <Pill tone="warning">{`Not in the facts: ${note.unverified.join(", ")}`}</Pill>
              : null}
            {note.truncated && <Pill tone="warning">Cut short by the provider: write again</Pill>}
            <span>{note.provider} · {note.model}</span>
            <MiniButton icon="refresh" disabled={busy} onClick={write}>{busy ? "Writing…" : "Write again"}</MiniButton>
          </div>
          <details>
            <summary style={{ cursor: "pointer", fontSize: "var(--fs-sm)", color: "var(--text-secondary)" }}>What was sent to AI</summary>
            <pre style={{ whiteSpace: "pre-wrap", fontSize: "var(--fs-xs)", background: "var(--surface-2)",
                          padding: 10, borderRadius: 6, margin: "6px 0 0" }}>{note.sent}</pre>
          </details>
        </div>)}
    </Card>
  );
}

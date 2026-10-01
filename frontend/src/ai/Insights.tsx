import { useEffect, useRef, useState } from "react";
import { Button, Card, Empty, MiniButton, Pill } from "../components/ui";
import { longDate } from "../format";
import { useApp, useAsync } from "../state";
import { Evidence, Insight, insightApi, prepareRateChange, Severity, taka } from "./api";
import { scenarioLink } from "./Scenario";

const para: React.CSSProperties = {
  margin: 0, fontSize: "var(--fs-base)", color: "var(--text-secondary)", lineHeight: 1.55,
};

/** Severity as a word and an icon, never colour alone. */
export function SeverityPill({ s }: { s: Severity }) {
  const tone = ({ critical: "critical", serious: "critical", warning: "warning", info: "info" } as const)[s];
  const label = { critical: "Critical", serious: "Serious", warning: "Watch", info: "Note" }[s];
  return <Pill tone={tone}>{label}</Pill>;
}

export function money(i: { money_at_stake: number | null; money_basis: string | null }) {
  if (i.money_at_stake == null) return null;
  return `${taka(i.money_at_stake)}${i.money_basis ? ` ${i.money_basis}` : ""}`;
}

/** Whether this reader may open a pre-filled rate change. The Rates page and
 *  the API enforce the same rule; this only decides whether to offer it. */
export function useCanDraftRates() {
  const { can, me } = useApp();
  return can("CONFIG_RATE_EDIT") && me?.scope_level === "HO";
}

function evidenceValue(e: Evidence) {
  if (e.value === "") return "—";
  const n = Number(e.value);
  switch (e.unit) {
    case "pct": {
      // Two decimals, or three where the market quotes three (8.645%).
      const s = n.toFixed(3);
      return `${s.endsWith("0") ? s.slice(0, -1) : s}%`;
    }
    case "bp": return `${n > 0 ? "+" : ""}${n} bp`;
    case "bdt": return taka(n);
    case "pct_change": return `${n > 0 ? "+" : ""}${n.toFixed(1)}%`;
    case "date": return longDate(e.value);
    default: return e.value;
  }
}

const GROUPS: [string, string, (i: Insight) => boolean][] = [
  ["all", "All", () => true],
  ["decide", "Decisions", (i) => i.action != null || i.severity === "critical" || i.severity === "serious"],
  ["market", "Market", (i) => i.scope === "PUBLIC"],
  ["book", "Our book", (i) => i.scope !== "PUBLIC" && i.kind !== "benchmark_drift"],
];

export default function InsightFeed({ focus }: { focus?: number | null }) {
  const { refreshAi, me } = useApp();
  const [tick, setTick] = useState(0);
  const [tab, setTab] = useState<"active" | "resolved">("active");
  const [group, setGroup] = useState("all");
  const [busy, setBusy] = useState(false);
  const feed = useAsync(() => insightApi.list({ status: tab }), [tab, tick]);
  const reload = () => { setTick((t) => t + 1); refreshAi(); };

  const items = feed.data?.items ?? [];
  const shown = items.filter(GROUPS.find(([k]) => k === group)![2]);
  const unread = items.filter((i) => !i.read).length;

  const readAll = async () => {
    setBusy(true);
    try { await insightApi.read("all"); reload(); } finally { setBusy(false); }
  };
  const refresh = async () => {
    setBusy(true);
    try { await insightApi.refresh(); reload(); } finally { setBusy(false); }
  };

  return (
    <Card title="What needs attention"
          subtitle="Found by rules over your book and the market, largest money at stake first within each level"
          actions={<div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            <div style={{ display: "flex", gap: 2 }}>
              <MiniButton active={tab === "active"} onClick={() => setTab("active")}>Open</MiniButton>
              <MiniButton active={tab === "resolved"} onClick={() => setTab("resolved")}
                          title="Conditions that have since cleared">Cleared</MiniButton>
            </div>
            {tab === "active" && unread > 0 &&
              <MiniButton disabled={busy} onClick={readAll}>Mark all read</MiniButton>}
            {me?.scope_level === "HO" &&
              <MiniButton icon="refresh" disabled={busy} onClick={refresh}
                          title="Run the checks now instead of waiting for the next change">Check now</MiniButton>}
          </div>}
          footnote="Each item comes from a fixed rule with a stated threshold, computed from the platform's own figures and market data. Rates are never changed automatically.">
      <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginBottom: 8 }}>
        {GROUPS.map(([k, label, f]) => (
          <MiniButton key={k} active={group === k} onClick={() => setGroup(k)}>
            {label} <span className="tnum" style={{ color: "var(--text-muted)" }}>{items.filter(f).length}</span>
          </MiniButton>))}
      </div>
      {feed.error && <p style={para}>{feed.error}</p>}
      {feed.data && shown.length === 0 && (
        <Empty title={tab === "active" ? "Nothing needs attention here" : "Nothing has cleared recently"}
               hint={tab === "active" ? "The checks run again whenever data, rates or the market change." : undefined} />
      )}
      <div style={{ display: "flex", flexDirection: "column" }}>
        {shown.map((i) => <InsightRow key={i.id} i={i} focused={focus === i.id} onChange={reload} />)}
      </div>
    </Card>
  );
}

function InsightRow({ i, focused, onChange }: { i: Insight; focused: boolean; onChange: () => void }) {
  const [open, setOpen] = useState(focused);
  const [useful, setUseful] = useState(i.useful);
  const ref = useRef<HTMLElement>(null);
  const canDraft = useCanDraftRates();
  const { can } = useApp();

  useEffect(() => {
    if (!focused) return;
    setOpen(true);
    ref.current?.scrollIntoView({ behavior: "smooth", block: "center" });
    if (!i.read) insightApi.read([i.id]).then(onChange).catch(() => {});
    // Only when focus arrives, not on every refresh.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focused]);

  const toggle = () => {
    setOpen((o) => !o);
    if (!open && !i.read) insightApi.read([i.id]).then(onChange).catch(() => {});
  };
  const vote = async (v: boolean) => {
    const next = useful === v ? null : v;
    setUseful(next);
    await insightApi.feedback(i.id, { useful: next }).catch(() => setUseful(useful));
  };
  const dismiss = async () => {
    await insightApi.feedback(i.id, { dismissed: true });
    onChange();
  };
  const m = money(i);

  return (
    <article ref={ref} style={{
      padding: "12px 4px", borderTop: "1px solid var(--border)", scrollMarginTop: 80,
      ...(focused ? { background: "color-mix(in srgb, var(--accent) 6%, transparent)", borderRadius: 8 } : {}),
    }}>
      <div style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
        <div style={{ paddingTop: 1 }}><SeverityPill s={i.severity} /></div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <button type="button" onClick={toggle} aria-expanded={open} style={{
            all: "unset", cursor: "pointer", display: "block", width: "100%",
            fontSize: "var(--fs-md)", fontWeight: 600, color: "var(--text-primary)", lineHeight: 1.4,
          }}>
            {!i.read && i.status === "active" && (
              <span aria-label="unread" title="New since you last looked" style={{
                display: "inline-block", width: 8, height: 8, borderRadius: 99, marginRight: 7,
                background: "var(--accent-solid)", verticalAlign: "middle" }} />)}
            {i.title}
          </button>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 2,
                        fontSize: "var(--fs-sm)", color: "var(--text-muted)" }}>
            {m && <span className="tnum" style={{ color: "var(--text-secondary)", fontWeight: 600 }}>{m}</span>}
            <span>{i.scope === "PUBLIC" ? "Market" : i.scope === "HO" ? "Whole bank" : "Your division"}</span>
            <span>{i.status === "resolved" && i.resolved_at
              ? `cleared ${longDate(i.resolved_at.slice(0, 10))}`
              : `raised ${longDate(i.raised_at.slice(0, 10))}`}</span>
          </div>
          {open && (
            <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 10 }}>
              <p style={para}>{i.body}</p>
              {i.evidence.length > 0 && (
                <dl style={{ margin: 0, display: "grid", gap: "4px 16px",
                             gridTemplateColumns: "repeat(auto-fill, minmax(170px, 1fr))" }}>
                  {i.evidence.filter((e) => e.value !== "").map((e) => (
                    <div key={e.label}>
                      <dt style={{ fontSize: "var(--fs-xs)", color: "var(--text-muted)" }}>{e.label}</dt>
                      <dd className="tnum" style={{ margin: 0, fontSize: "var(--fs-base)",
                                                    color: "var(--text-primary)" }}>{evidenceValue(e)}</dd>
                    </div>))}
                </dl>
              )}
              {i.sources.length > 0 && (
                <p style={{ ...para, fontSize: "var(--fs-sm)", color: "var(--text-muted)" }}>
                  Source: {i.sources.map((s, n) => (
                    <span key={s.label}>{n > 0 && " · "}
                      {s.url ? <a href={s.url} target="_blank" rel="noreferrer">{s.label} ↗</a> : s.label}
                    </span>))}
                </p>
              )}
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
                {i.action?.type === "prepare_rate_change" && i.status === "active" && canDraft && (
                  <Button variant="primary" size="sm" icon="percent"
                          onClick={() => i.action?.type === "prepare_rate_change" && prepareRateChange(i.action)}>
                    Prepare rate change to {Number(i.action.suggested).toFixed(2)}%
                  </Button>)}
                {i.action?.type === "scenario" && i.status === "active" && can("SCENARIO_RUN") && (
                  <Button variant="primary" size="sm" icon="layers"
                          onClick={() => { if (i.action?.type === "scenario") location.hash = scenarioLink(i.action.scenario); }}>
                    {i.action.label}
                  </Button>)}
                <span style={{ flex: 1 }} />
                <span style={{ fontSize: "var(--fs-sm)", color: "var(--text-muted)" }}>Useful?</span>
                <MiniButton active={useful === true} onClick={() => vote(true)} title="Useful">Yes</MiniButton>
                <MiniButton active={useful === false} onClick={() => vote(false)} title="Not useful to me">No</MiniButton>
                {i.status === "active" &&
                  <MiniButton onClick={dismiss} title="Hide this for you; it returns if it gets worse">Dismiss</MiniButton>}
              </div>
            </div>
          )}
        </div>
      </div>
    </article>
  );
}

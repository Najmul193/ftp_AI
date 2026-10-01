import { useEffect, useState } from "react";
import { Icon } from "../components/icons";
import { MiniButton, Pill } from "../components/ui";
import { useApp } from "../state";
import { openAsk } from "./Ask";
import { fmtUnit } from "./Cone";
import { BookOutlook, Insight, insightApi, outlookApi, PolicyOutlook } from "./api";
import { scenarioLink } from "./Scenario";

const muted: React.CSSProperties = { color: "var(--text-muted)", fontSize: "var(--fs-xs)" };
const KEY = "ftp_home_card";

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
}

const SEV_RANK: Record<string, number> = { critical: 0, serious: 1, warning: 2, info: 3 };

/**
 * The copilot on the landing page: one strip that says where this month is
 * heading, which way the policy rate leans, the single finding worth most,
 * and a box to ask anything. Collapsible; remembered per browser.
 */
export default function HomeCard() {
  const { me, ai, can } = useApp();
  const [open, setOpen] = useState(() => {
    try { return localStorage.getItem(KEY) !== "0"; } catch { return true; }
  });
  const [book, setBook] = useState<BookOutlook | null>(null);
  const [policy, setPolicy] = useState<PolicyOutlook | null>(null);
  const [top, setTop] = useState<Insight | null>(null);
  const [q, setQ] = useState("");

  useEffect(() => { try { localStorage.setItem(KEY, open ? "1" : "0"); } catch { /* private mode */ } }, [open]);
  useEffect(() => {
    if (!open) return;
    outlookApi.book().then(setBook).catch(() => undefined);
    outlookApi.policy().then(setPolicy).catch(() => undefined);
    insightApi.list().then((r) => {
      const best = [...r.items].filter((i) => !i.dismissed)
        .sort((a, b) => (SEV_RANK[a.severity] - SEV_RANK[b.severity])
          || Number(b.money_at_stake ?? 0) - Number(a.money_at_stake ?? 0))[0];
      setTop(best ?? null);
    }).catch(() => undefined);
  }, [open]);

  const profit = book?.metrics.find((m) => m.metric === "net_ftp_profit");
  const deposits = book?.metrics.find((m) => m.metric === "deposits");
  const first = (me?.full_name || me?.username || "").split(" ")[0];

  if (!open) {
    return (
      <button type="button" onClick={() => setOpen(true)} className="btn btn-ghost"
              style={{ display: "inline-flex", gap: 6, alignItems: "center", marginBottom: 10,
                       color: "var(--accent)", fontSize: "var(--fs-sm)" }}>
        <Icon name="sparkle" size={14} /> Show the copilot</button>);
  }

  const cell: React.CSSProperties = { display: "flex", flexDirection: "column", gap: 2, minWidth: 0 };
  return (
    <section aria-label="Copilot" style={{
      marginBottom: 14, padding: "12px 16px", borderRadius: "var(--radius)",
      border: "1px solid color-mix(in srgb, var(--accent) 30%, var(--border))",
      background: "linear-gradient(100deg, color-mix(in srgb, var(--accent) 9%, var(--surface-1)), var(--surface-1) 60%)",
      boxShadow: "var(--shadow-sm)",
      display: "grid", gridTemplateColumns: "minmax(170px, 1.1fr) repeat(3, minmax(150px, 1fr)) minmax(220px, 1.4fr) auto",
      gap: 18, alignItems: "center",
    }}>
      <div style={cell}>
        <span style={{ display: "flex", gap: 6, alignItems: "center", fontWeight: 700 }}>
          <Icon name="sparkle" size={15} />{greeting()}{first ? `, ${first}` : ""}</span>
        <span style={muted}>{book?.available ? `${book.label} · data to ${book.latest}` : "Your copilot"}</span>
      </div>

      <a href="#/outlook" style={{ ...cell, textDecoration: "none", color: "inherit" }}
         title="Forecast from the book's own history; open the Outlook for the range and the chart">
        <span style={muted}>FTP profit this month, likely</span>
        <b className="tnum" style={{ fontSize: 18 }}>{profit?.month ? fmtUnit(profit.month.p50, "bdt") : "—"}</b>
        <span style={muted} className="tnum">{profit?.month
          ? `${fmtUnit(profit.month.p10, "bdt")} – ${fmtUnit(profit.month.p90, "bdt")}` : ""}</span>
      </a>

      <a href="#/outlook" style={{ ...cell, textDecoration: "none", color: "inherit" }}>
        <span style={muted}>Deposits at month-end, likely</span>
        <b className="tnum" style={{ fontSize: 18 }}>{deposits?.month ? fmtUnit(deposits.month.p50, "bdt") : "—"}</b>
        <span style={muted} className="tnum">{deposits ? `today ${fmtUnit(deposits.last.value, "bdt")}` : ""}</span>
      </a>

      <a href="#/outlook" style={{ ...cell, textDecoration: "none", color: "inherit" }}>
        <span style={muted}>Next MPC{policy?.next_meeting ? ` · ${new Date(`${policy.next_meeting}T00:00:00`).toLocaleDateString("en-GB", { day: "numeric", month: "short" })}` : ""}</span>
        <b style={{ fontSize: 18, textTransform: "capitalize" }}>{policy ? `${policy.leaning} ${policy.odds[policy.leaning]}%` : "—"}</b>
        <span style={muted}>{policy ? `cut ${policy.odds.cut}% · hike ${policy.odds.hike}%` : ""}</span>
      </a>

      <div style={cell}>
        <span style={muted}>Worth acting on</span>
        {top ? (
          <>
            <a href={`#/intel?focus=${top.id}`} style={{ fontWeight: 600, fontSize: "var(--fs-sm)", lineHeight: 1.35,
                                                        color: "var(--text-primary)" }}>{top.title}</a>
            <span style={{ display: "flex", gap: 6, alignItems: "center" }}>
              <Pill tone={top.severity === "critical" ? "critical" : top.severity === "info" ? "info" : "warning"}>
                {top.severity}</Pill>
              {top.action?.type === "scenario" && can("SCENARIO_RUN") && (
                <a href={scenarioLink(top.action.scenario)} style={{ fontSize: "var(--fs-xs)", fontWeight: 600 }}>
                  Simulate →</a>)}
            </span>
          </>) : <span style={muted}>Nothing pressing.</span>}
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 6, alignItems: "flex-end" }}>
        {ai?.can_chat && (
          <form onSubmit={(e) => { e.preventDefault(); if (q.trim()) { openAsk(q.trim(), undefined, true); setQ(""); } }}
                style={{ display: "flex", gap: 4 }}>
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Ask anything…"
                   aria-label="Ask the copilot"
                   style={{ width: 170, padding: "6px 10px", borderRadius: 8, border: "1px solid var(--border)",
                            background: "var(--surface-1)", color: "var(--text-primary)", font: "inherit",
                            fontSize: "var(--fs-sm)" }} />
          </form>)}
        <MiniButton onClick={() => setOpen(false)} title="Hide the copilot strip">Hide</MiniButton>
      </div>
    </section>
  );
}

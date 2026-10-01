import { Card, Empty, Grid, MiniButton, Pill } from "../components/ui";
import { useApp, useAsync } from "../state";
import { useSpeech } from "./Brief";
import Cone, { fmtUnit } from "./Cone";
import { Pulse as PulseData, PulseItem, pulseApi, taka } from "./api";
import { Confidence } from "./Outlook";
import { scenarioLink } from "./Scenario";

const muted: React.CSSProperties = { color: "var(--text-muted)", fontSize: "var(--fs-xs)" };

const tone = (s: number) => (s >= 65 ? "var(--status-good)" : s >= 45 ? "var(--status-warning)"
  : "var(--status-critical)");

function Dial({ score, grade }: { score: number; grade: string }) {
  const r = 70, c = 2 * Math.PI * r;
  return (
    <svg viewBox="0 0 180 180" width={180} height={180} role="img" aria-label={`Health ${score} of 100, grade ${grade}`}>
      <circle cx={90} cy={90} r={r} fill="none" stroke="var(--border)" strokeWidth={14} />
      <circle cx={90} cy={90} r={r} fill="none" stroke={tone(score)} strokeWidth={14} strokeLinecap="round"
              strokeDasharray={`${(score / 100) * c} ${c}`} transform="rotate(-90 90 90)" />
      <text x={90} y={86} textAnchor="middle" fontSize={44} fontWeight={750} fill="var(--text-primary)">{score}</text>
      <text x={90} y={116} textAnchor="middle" fontSize={15} fill="var(--text-muted)">grade {grade}</text>
    </svg>
  );
}

/** What the page says, in sentences, for reading aloud. */
function summary(d: PulseData) {
  const weakest = [...d.parts].sort((a, b) => a.score - b.score)[0];
  const strongest = [...d.parts].sort((a, b) => b.score - a.score)[0];
  const np = d.outlook?.metrics.find((m) => m.metric === "net_ftp_profit");
  return [
    `${d.label}: health ${d.score} out of 100, grade ${d.grade}.`,
    strongest ? `Strongest: ${strongest.label.toLowerCase()}, ${strongest.value}.` : "",
    weakest ? `Weakest: ${weakest.label.toLowerCase()}, ${weakest.value}.` : "",
    np?.month ? `This month's FTP profit is likely ${fmtUnit(np.month.p50, "bdt")}.` : "",
    d.risks[0] ? `Top risk: ${d.risks[0].title}.` : "",
    d.openings[0] ? `Top opening: ${d.openings[0].title}.` : "",
  ].filter(Boolean).join(" ");
}

function Items({ items, empty }: { items: PulseItem[]; empty: string }) {
  const { can } = useApp();
  if (!items.length) return <p style={muted}>{empty}</p>;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {items.map((i) => (
        <div key={i.id} style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
          <Pill tone={i.severity === "critical" || i.severity === "serious" ? "critical"
            : i.severity === "warning" ? "warning" : "info"}>{i.severity}</Pill>
          <div style={{ flex: 1, minWidth: 0 }}>
            <a href={`#/intel?focus=${i.id}`} style={{ fontWeight: 600, color: "var(--text-primary)" }}>{i.title}</a>
            {i.money != null && <div style={muted} className="tnum">{taka(i.money)} {i.basis ?? ""}</div>}
          </div>
          {i.action?.type === "scenario" && can("SCENARIO_RUN") && (
            <MiniButton onClick={() => { if (i.action?.type === "scenario") location.hash = scenarioLink(i.action.scenario); }}>
              Simulate</MiniButton>)}
        </div>))}
    </div>
  );
}

export default function Pulse() {
  const p = useAsync(() => pulseApi.get(), []);
  const { speak, speaking, supported } = useSpeech();
  if (p.error) return <Card><Empty title="Could not take the pulse" hint={p.error} /></Card>;
  const d = p.data;
  if (!d) return <p style={muted}>Taking the pulse…</p>;
  if (!d.available) return <Card><Empty title="No pulse yet" hint={d.reason} /></Card>;
  const np = d.outlook?.metrics.find((m) => m.metric === "net_ftp_profit");
  const dep = d.outlook?.metrics.find((m) => m.metric === "deposits");
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <Card title={`${d.label} · health`}
            subtitle={`Data to ${d.as_of}. A weighted score of the parts on the right; each is explained.`}
            actions={supported && <MiniButton icon="speaker" onClick={() => speak(summary(d), "en")}>
              {speaking ? "Stop" : "Listen"}</MiniButton>}>
        <Grid cols="200px minmax(0, 1fr)" gap={24}>
          <div style={{ display: "grid", placeItems: "center" }}><Dial score={d.score} grade={d.grade} /></div>
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            {d.parts.map((x) => (
              <div key={x.key} title={x.explain}>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 10, fontSize: "var(--fs-sm)" }}>
                  <b>{x.label}</b><span className="tnum" style={muted}>{x.value}</span>
                </div>
                <div style={{ height: 8, borderRadius: 4, background: "var(--border)", marginTop: 4 }}>
                  <div style={{ width: `${x.score}%`, height: 8, borderRadius: 4, background: tone(x.score) }} />
                </div>
                <div style={muted}>{x.explain}</div>
              </div>))}
          </div>
        </Grid>
      </Card>

      <Grid cols="repeat(auto-fit, minmax(320px, 1fr))" gap={16}>
        <Card title="Against the industry"
              subtitle={`Our last 30 days against all scheduled banks' weighted averages${d.industry_month ? ` (Bangladesh Bank, ${new Date(`${d.industry_month}T00:00:00`).toLocaleDateString("en-GB", { month: "long", year: "numeric" })})` : ""}.`}>
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            {d.versus.map((v) => {
              const diff = v.ours != null && v.industry != null ? Number(v.ours) - Number(v.industry) : null;
              const good = diff == null ? null : v.better === "lower" ? diff < 0 : diff > 0;
              return (
                <div key={v.label} style={{ display: "grid", gridTemplateColumns: "1fr auto auto", gap: 12, alignItems: "baseline" }}>
                  <span style={{ fontWeight: 600 }}>{v.label}</span>
                  <span className="tnum" style={{ fontSize: 20, fontWeight: 700 }}>{v.ours != null ? `${Number(v.ours).toFixed(2)}%` : "—"}</span>
                  <span className="tnum" style={{ ...muted, minWidth: 130, textAlign: "right" }}>
                    industry {v.industry != null ? `${Number(v.industry).toFixed(2)}%` : "—"}
                    {diff != null && <b style={{ color: good ? "var(--delta-up)" : "var(--delta-down)", marginLeft: 6 }}>
                      {diff > 0 ? "+" : ""}{Math.round(diff * 100)} bp</b>}
                  </span>
                </div>);
            })}
          </div>
        </Card>
        <Card title="Where this month lands" subtitle="Forecast from the book's own history, with the likely range.">
          {np?.month && (
            <div style={{ display: "flex", gap: 18, flexWrap: "wrap", marginBottom: 8 }}>
              <div><div style={muted}>FTP profit, the month</div>
                <b className="tnum" style={{ fontSize: 20 }}>{fmtUnit(np.month.p50, "bdt")}</b>
                <div style={muted} className="tnum">{fmtUnit(np.month.p10, "bdt")} – {fmtUnit(np.month.p90, "bdt")}</div></div>
              {dep?.month && <div><div style={muted}>Deposits at month-end</div>
                <b className="tnum" style={{ fontSize: 20 }}>{fmtUnit(dep.month.p50, "bdt")}</b>
                <div style={muted} className="tnum">today {fmtUnit(dep.last.value, "bdt")}</div></div>}
              <span><Confidence c={np.confidence} why={np.confidence_reason} /></span>
            </div>)}
          {dep && <Cone history={dep.history} forecast={dep.forecast.slice(0, 60)} unit="bdt" height={170}
                        ariaLabel="Deposits: history and forecast" />}
        </Card>
      </Grid>

      <Grid cols="repeat(auto-fit, minmax(320px, 1fr))" gap={16}>
        <Card title="Top risks" subtitle="The open findings with the most money behind them.">
          <Items items={d.risks} empty="No open risks." /></Card>
        <Card title="Top openings" subtitle="Where pricing or pace leaves money to gain.">
          <Items items={d.openings} empty="No openings found right now." /></Card>
      </Grid>
    </div>
  );
}

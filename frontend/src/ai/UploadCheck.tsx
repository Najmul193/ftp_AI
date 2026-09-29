import { Button, Pill } from "../components/ui";
import { longDate, shortDate } from "../format";
import { ApiError } from "../api";
import { useApp, useAsync } from "../state";
import { UploadReview, uploadCheckApi } from "./api";

const para: React.CSSProperties = {
  margin: 0, fontSize: "var(--fs-base)", color: "var(--text-secondary)", lineHeight: 1.5,
};

/** What an upload got wrong, how to fix it, and what looks odd against the day
 *  before. Computed from the platform's own records: no AI provider is called
 *  and nothing about the file leaves the bank. Shown only while AI is on. */
export default function UploadCheck({ batchRef, onDelete }: {
  batchRef: string; onDelete?: (ref: string) => void;
}) {
  const { ai } = useApp();
  const on = Boolean(ai?.enabled);
  // A batch deleted from this page is gone: say nothing rather than fail.
  const r = useAsync(() => (on ? uploadCheckApi.review(batchRef).catch((e) => {
    if (e instanceof ApiError && e.status === 404) return null;
    throw e;
  }) : Promise.resolve(null)), [batchRef, on]);
  if (!on || (!r.data && !r.error && !r.loading)) return null;
  const d: UploadReview | null = r.data;

  const findings = d?.days.flatMap((day) => day.findings.map((f) => ({ ...f, date: day.date }))) ?? [];
  const tone = !d ? "neutral" : d.serious ? "critical" : d.warnings || d.rules.some((x) => x.severity === "REJECT")
    ? "warning" : "good";

  return (
    <section aria-label="Upload check" style={{
      marginTop: 14, padding: "12px 14px", borderRadius: 8, border: "1px solid var(--border)",
      background: "var(--surface-2)", display: "flex", flexDirection: "column", gap: 10,
    }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <b style={{ fontSize: "var(--fs-base)" }}>Upload check</b>
        {d && <Pill tone={tone}>{d.serious ? "Check before relying on it" : tone === "good" ? "Looks right" : "Worth a look"}</Pill>}
        <span style={{ flex: 1 }} />
        <span style={{ fontSize: "var(--fs-xs)", color: "var(--text-muted)" }}>From the platform's own records; nothing is sent to AI</span>
      </div>
      {r.error && <p style={para}>{r.error}</p>}
      {!d && !r.error && <p style={para}>Checking…</p>}
      {d && <p style={{ ...para, color: "var(--text-primary)", fontWeight: 600 }}>{d.headline}</p>}

      {d?.days.filter((x) => x.note).map((x) => (
        <p key={x.date} style={{ ...para, fontSize: "var(--fs-sm)" }}>{shortDate(x.date)}: {x.note}</p>))}

      {findings.length > 0 && (
        <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: 8 }}>
          {findings.map((f, i) => (
            <li key={i} style={{ display: "flex", gap: 8, alignItems: "flex-start" }}>
              <Pill tone={f.severity === "serious" ? "critical" : f.severity === "warning" ? "warning" : "info"}>
                {f.severity === "serious" ? "Serious" : f.severity === "warning" ? "Check" : "Note"}</Pill>
              <div>
                <div style={{ fontWeight: 600, color: "var(--text-primary)" }}>
                  {f.title}{d!.days.length > 1 && <span style={{ fontWeight: 400, color: "var(--text-muted)" }}> · {shortDate(f.date)}</span>}
                </div>
                <p style={{ ...para, fontSize: "var(--fs-sm)" }}>{f.detail}</p>
              </div>
            </li>))}
        </ul>)}
      {d && d.serious > 0 && onDelete && (
        <div>
          <Button variant="danger" size="sm" onClick={() => onDelete(d.batch_ref)}>
            This day is wrong: delete batch {d.batch_ref}</Button>
        </div>)}

      {d && d.rules.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <div style={{ fontSize: "var(--fs-xs)", fontWeight: 700, letterSpacing: ".06em",
                        textTransform: "uppercase", color: "var(--text-muted)" }}>What the checks found</div>
          {d.rules.map((x) => (
            <details key={`${x.rule}-${x.severity}`} open={x.severity === "REJECT"}>
              <summary style={{ cursor: "pointer", fontSize: "var(--fs-base)" }}>
                <Pill tone={x.severity === "REJECT" ? "critical" : x.severity === "WARN" ? "warning" : "neutral"}>
                  {x.severity === "REJECT" ? "Not loaded" : x.severity === "WARN" ? "Loaded, flagged" : "Advisory"}</Pill>{" "}
                <b>{x.title}</b>{" "}
                <span className="tnum" style={{ color: "var(--text-muted)" }}>
                  {x.rows ? `${x.rows.toLocaleString("en-IN")} row${x.rows === 1 ? "" : "s"}`
                    : `${x.findings.toLocaleString("en-IN")} finding${x.findings === 1 ? "" : "s"}`}</span>
              </summary>
              <div style={{ padding: "6px 0 4px 4px", display: "flex", flexDirection: "column", gap: 4 }}>
                <p style={para}>{x.meaning}</p>
                <p style={para}><b>Fix:</b> {x.fix}{x.link && <> <a href={x.link.href}>{x.link.label} →</a></>}</p>
                {x.values.length > 0 && (
                  <p style={{ ...para, fontSize: "var(--fs-sm)" }}>
                    Values: {x.values.map((v) => <code key={v.value} style={{ marginRight: 8 }}>{v.value} ×{v.count}</code>)}</p>)}
                {x.examples.length > 0 && (
                  <p style={{ ...para, fontSize: "var(--fs-sm)", color: "var(--text-muted)" }}>
                    e.g. {x.examples.map((e) => `${e.where}: ${e.message}`).join(" · ")}</p>)}
              </div>
            </details>))}
        </div>)}
      {d && d.days.length > 0 && (
        <p style={{ ...para, fontSize: "var(--fs-xs)", color: "var(--text-muted)" }}>
          Compared with {d.days.map((x) => x.compared_with ? longDate(x.compared_with) : "—").join(", ")}
          {d.more_dates && ` (latest ${d.screened_dates} dates of this file)`}.
        </p>)}
    </section>
  );
}

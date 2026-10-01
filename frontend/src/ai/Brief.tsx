import { Fragment, ReactNode, useEffect, useState } from "react";
import { Button, Card, MiniButton, Pill } from "../components/ui";
import { longDate, shortDate } from "../format";
import { useApp, useAsync } from "../state";
import { Brief, BriefDecision, insightApi, prepareRateChange } from "./api";
import { scenarioLink } from "./Scenario";
import { money, SeverityPill, useCanDraftRates } from "./Insights";

const para: React.CSSProperties = {
  margin: 0, fontSize: "var(--fs-base)", color: "var(--text-secondary)", lineHeight: 1.6,
};
const heading: React.CSSProperties = {
  margin: "0 0 6px", fontSize: "var(--fs-xs)", fontWeight: 700, letterSpacing: ".06em",
  textTransform: "uppercase", color: "var(--text-muted)",
};

/** The write-up is plain text with **bold** headings and "- " / "1. " lines:
 *  rendered as text, never as HTML, since it came from outside the bank. */
export function Narrative({ text }: { text: string }) {
  const bold = (s: string): ReactNode[] =>
    s.split(/(\*\*[^*]+\*\*)/).map((part, n) => part.startsWith("**") && part.endsWith("**")
      ? <b key={n} style={{ color: "var(--text-primary)" }}>{part.slice(2, -2)}</b>
      : <Fragment key={n}>{part}</Fragment>);
  const lines = text.split("\n").map((l) => l.trim()).filter(Boolean);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      {lines.map((l, n) => {
        if (/^\*\*[^*]+\*\*:?$/.test(l))
          return <h4 key={n} style={{ ...heading, marginTop: n ? 10 : 0 }}>{l.replace(/\*|:$/g, "")}</h4>;
        const bullet = l.match(/^(?:[-•]|\d+\.)\s+(.*)$/);
        if (bullet)
          return <p key={n} style={{ ...para, paddingLeft: 14, textIndent: -10 }}>
            <span aria-hidden style={{ color: "var(--text-muted)" }}>• </span>{bold(bullet[1])}</p>;
        return <p key={n} style={{ ...para, fontSize: n === 0 ? "var(--fs-md)" : undefined,
                                   color: n === 0 ? "var(--text-primary)" : undefined,
                                   fontWeight: n === 0 ? 600 : undefined }}>{bold(l)}</p>;
      })}
    </div>
  );
}

/** Read aloud with the browser's own voice: nothing leaves the machine. */
export function useSpeech() {
  const [speaking, setSpeaking] = useState(false);
  const supported = typeof window !== "undefined" && "speechSynthesis" in window;
  useEffect(() => () => { if (supported) window.speechSynthesis.cancel(); }, [supported]);
  const speak = (text: string, lang: "en" | "bn") => {
    if (!supported) return;
    const synth = window.speechSynthesis;
    if (speaking) { synth.cancel(); setSpeaking(false); return; }
    const u = new SpeechSynthesisUtterance(text.replace(/\*\*/g, "").replace(/৳/g, "taka "));
    u.lang = lang === "bn" ? "bn-BD" : "en-IN";
    u.rate = 1;
    u.onend = u.onerror = () => setSpeaking(false);
    setSpeaking(true);
    synth.speak(u);
  };
  return { speak, speaking, supported };
}

function Decision({ d, n }: { d: BriefDecision; n: number }) {
  const canDraft = useCanDraftRates();
  const { can } = useApp();
  const m = money(d);
  return (
    <li style={{ display: "flex", gap: 10, alignItems: "flex-start", padding: "8px 0",
                 borderTop: n ? "1px solid var(--border)" : undefined }}>
      <span className="tnum" aria-hidden style={{
        width: 22, height: 22, borderRadius: 99, flexShrink: 0, display: "grid", placeItems: "center",
        background: "var(--accent-soft)", color: "var(--accent)", fontSize: "var(--fs-xs)", fontWeight: 700,
      }}>{n + 1}</span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontWeight: 600, color: "var(--text-primary)", lineHeight: 1.4 }}>{d.title}</div>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginTop: 3,
                      fontSize: "var(--fs-sm)", color: "var(--text-muted)" }}>
          <SeverityPill s={d.severity} />
          {m && <span className="tnum" style={{ color: "var(--text-secondary)", fontWeight: 600 }}>{m}</span>}
          {d.insight_id && <a href={`#/intel?focus=${d.insight_id}`}>Why?</a>}
        </div>
      </div>
      {d.action?.type === "prepare_rate_change" && canDraft && (
        <MiniButton icon="percent"
                    onClick={() => d.action?.type === "prepare_rate_change" && prepareRateChange(d.action)}
                    title={`Open Rate configuration with ${Number(d.action.suggested).toFixed(2)}% filled in`}>
          Prepare
        </MiniButton>)}
      {d.action?.type === "scenario" && can("SCENARIO_RUN") && (
        <MiniButton icon="layers" title={d.action.label}
                    onClick={() => { if (d.action?.type === "scenario") location.hash = scenarioLink(d.action.scenario); }}>
          Simulate
        </MiniButton>)}
    </li>
  );
}

export default function BriefCard() {
  const [lang, setLang] = useState<"en" | "bn">(() =>
    (localStorage.getItem("ftp_brief_lang") as "en" | "bn") || "en");
  const [tick, setTick] = useState(0);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [view, setView] = useState<"ai" | "standard" | null>(null);
  const [showUnverified, setShowUnverified] = useState(false);
  const res = useAsync(() => insightApi.brief(lang), [lang, tick]);
  const { speak, speaking, supported } = useSpeech();

  useEffect(() => { try { localStorage.setItem("ftp_brief_lang", lang); } catch { /* private mode */ } }, [lang]);

  const b: Brief | null = res.data;
  const write = async (rewrite = false) => {
    setBusy(true); setErr(null);
    try { await insightApi.writeBrief(lang, rewrite); setView("ai"); setTick((t) => t + 1); }
    catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  };

  if (!b) {
    return <Card title="Morning brief">
      <p style={para}>{res.error ?? "Preparing the brief…"}</p></Card>;
  }

  const ai = b.ai;
  // An AI write-up with a figure the checks could not find in the facts is
  // not shown by default: a wrong number in a bank reads exactly like a right one.
  const trusted = ai != null && ai.grounded !== false;
  const current = view ?? (trusted ? "ai" : lang === "bn" && ai ? "ai" : "standard");
  const showAi = current === "ai" && ai != null && (trusted || showUnverified);
  const s = b.standard;

  const spoken = showAi ? ai!.narrative
    : [s.headline, ...s.sections.flatMap((x) => [x.title, ...x.lines]),
       s.decisions.length ? "Decisions for today." : "",
       ...s.decisions.map((d, n) => `${n + 1}. ${d.title}. ${money(d) ?? ""}`)].join(". ");

  const subtitle = [
    `For ${b.scope_label}`,
    longDate(s.as_of.today),
    s.as_of.business_date && `bank data to ${shortDate(s.as_of.business_date)}`,
    s.as_of.market && `market to ${shortDate(s.as_of.market)}`,
  ].filter(Boolean).join(" · ");

  return (
    <Card title="Morning brief" subtitle={subtitle}
          actions={<div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
            <div style={{ display: "flex", gap: 2 }} role="group" aria-label="Language">
              <MiniButton active={lang === "en"} onClick={() => { setLang("en"); setView(null); }}>EN</MiniButton>
              <MiniButton active={lang === "bn"} onClick={() => { setLang("bn"); setView(null); }}>বাংলা</MiniButton>
            </div>
            {ai && (
              <div style={{ display: "flex", gap: 2 }} role="group" aria-label="Version">
                <MiniButton active={current === "standard"} onClick={() => setView("standard")}
                            title="Composed by the platform from its own figures">Standard</MiniButton>
                <MiniButton active={current === "ai"} onClick={() => setView("ai")} icon="sparkle"
                            title="Written by the AI provider from the same facts">AI</MiniButton>
              </div>)}
            {supported && (
              <MiniButton icon="speaker" active={speaking} onClick={() => speak(spoken, showAi ? lang : "en")}
                          title="Read aloud with your browser's voice; nothing is sent anywhere">
                {speaking ? "Stop" : "Listen"}</MiniButton>)}
          </div>}>
      <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        {showAi ? (
          <>
            <Narrative text={ai!.narrative} />
            {s.decisions.length > 0 && (
              <section>
                <h4 style={heading}>Act on it</h4>
                <ol style={{ margin: 0, padding: 0, listStyle: "none" }}>
                  {s.decisions.map((d, n) => <Decision key={`${d.kind}:${d.subject}`} d={d} n={n} />)}
                </ol>
              </section>)}
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center",
                          fontSize: "var(--fs-sm)", color: "var(--text-muted)" }}>
              {ai!.grounded
                ? <Pill tone="good">Numbers verified against the facts</Pill>
                : ai!.grounded === false
                  ? <Pill tone="warning">{`${ai!.unverified.length} figure${ai!.unverified.length === 1 ? "" : "s"} not in the facts: ${ai!.unverified.join(", ")}`}</Pill>
                  : <Pill tone="neutral">Not checked</Pill>}
              {!ai!.current && <Pill tone="warning">Written before the latest data</Pill>}
              <span>{ai!.provider} · {ai!.model} · {new Date(ai!.created_at).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })}</span>
              {b.can_rewrite && (
                <MiniButton icon="refresh" disabled={busy} onClick={() => write(true)}>
                  {busy ? "Writing…" : "Rewrite"}</MiniButton>)}
            </div>
            <details>
              <summary style={{ cursor: "pointer", fontSize: "var(--fs-sm)", color: "var(--text-secondary)" }}>
                What was sent to AI
              </summary>
              <p style={{ ...para, fontSize: "var(--fs-sm)", margin: "6px 0" }}>
                Branches and divisions travel as tokens (BR_…, DIV_…), amounts are rounded to three
                figures in crore, and account-level data never leaves. This is the exact text the provider received.
              </p>
              <pre style={{ whiteSpace: "pre-wrap", fontSize: "var(--fs-xs)", maxHeight: 280, overflow: "auto",
                            background: "var(--surface-2)", padding: 10, borderRadius: 6, margin: 0 }}>{ai!.sent}</pre>
            </details>
          </>
        ) : (
          <>
            <p style={{ margin: 0, fontSize: "var(--fs-lg)", fontWeight: 650, color: "var(--text-primary)",
                        lineHeight: 1.35, letterSpacing: "-.01em" }}>{s.headline}</p>
            {ai && !trusted && !showUnverified && (
              <p style={para}>
                <Pill tone="warning">AI write-up held back</Pill>{" "}
                It contains {ai.unverified.length === 1 ? "a figure" : "figures"} ({ai.unverified.join(", ")}) that
                the platform could not find in the facts it sent, so the standard brief is shown instead.{" "}
                <button type="button" onClick={() => { setShowUnverified(true); setView("ai"); }} style={{
                  all: "unset", cursor: "pointer", color: "var(--accent)", textDecoration: "underline" }}>
                  Show it anyway</button>
              </p>)}
            {lang === "bn" && !ai && (
              <p style={para}>বাংলা সংস্করণটি AI দিয়ে লেখা হয়; নিচে আদর্শ (ইংরেজি) সংস্করণ দেখানো হচ্ছে।</p>)}
            <div style={{ display: "grid", gap: 18, gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))" }}>
              {s.sections.map((sec) => (
                <section key={sec.key}>
                  <h4 style={heading}>{sec.title}</h4>
                  <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "flex",
                               flexDirection: "column", gap: 4 }}>
                    {sec.lines.map((l) => <li key={l} className="tnum" style={para}>{l}</li>)}
                  </ul>
                </section>))}
            </div>
            {s.decisions.length > 0 && (
              <section>
                <h4 style={heading}>Decisions for today</h4>
                <ol style={{ margin: 0, padding: 0, listStyle: "none" }}>
                  {s.decisions.map((d, n) => <Decision key={`${d.kind}:${d.subject}`} d={d} n={n} />)}
                </ol>
              </section>)}
            {s.news.length > 0 && (
              <section>
                <h4 style={heading}>In the news</h4>
                {s.news.map((n) => (
                  <p key={n.url} style={{ ...para, fontSize: "var(--fs-sm)" }}>
                    <a href={n.url} target="_blank" rel="noreferrer">{n.title}</a>
                    <span style={{ color: "var(--text-muted)" }}> · {n.source}</span>
                  </p>))}
              </section>)}
            {b.can_write && (!ai || !ai.current) && (
              <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
                <Button icon="sparkle" disabled={busy} onClick={() => write(false)}>
                  {busy ? "Writing…" : lang === "bn" ? "বাংলায় লিখুন" : ai ? "Write it up again" : "Write it up with AI"}
                </Button>
                <span style={{ fontSize: "var(--fs-sm)", color: "var(--text-muted)" }}>
                  Through the privacy gateway: names become tokens, amounts are blurred, every figure is checked.
                </span>
              </div>)}
          </>
        )}
        {err && <p style={para}><Pill tone="critical">Not written</Pill> {err}</p>}
      </div>
    </Card>
  );
}

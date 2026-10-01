import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from "react";
import { Icon } from "../components/icons";
import { Button, IconButton, MiniButton, Pill } from "../components/ui";
import { shortDate } from "../format";
import { currentView, useApp } from "../state";
import { PAGE_LABELS, pageExtra } from "./pageContext";
import { AskEvent, askApi, AskPreset, AskResult, StoredMessage } from "./api";
import AnswerView from "./AnswerView";
import { Narrative } from "./Brief";

/** Open Ask FTP from anywhere, optionally with a question ready to send.
 *  With a `preset` (a plan a page built, e.g. a "Why?" button) it is sent at
 *  once and runs exactly as built, with no planning step. */
export function openAsk(question?: string, preset?: AskPreset, send = false) {
  window.dispatchEvent(new CustomEvent("ftp:ask", { detail: { question, preset, send } }));
}

interface Seed { question?: string; preset?: AskPreset; send?: boolean }

interface Step { n: number; tool: string; title: string; thought: string; result?: AskResult; refused?: string }

interface Turn {
  question: string;
  sent?: string;
  status?: string;
  result?: AskResult;
  /** A multi-step answer: each lookup, its reason and its result. */
  steps?: Step[];
  followups?: string[];
  /** Reveal the answer as it lands; a reloaded thread shows at once. */
  fresh?: boolean;
  answer?: Extract<AskEvent, { type: "answer" }>;
  stop?: { kind: "clarify" | "refused" | "error"; text: string };
  messageId?: number;
  pinnable?: boolean;
  pinned?: boolean;
  pinError?: string;
  done?: boolean;
}

const muted: React.CSSProperties = { fontSize: "var(--fs-sm)", color: "var(--text-muted)" };

export default function AskLauncher() {
  const { ai } = useApp();
  const [open, setOpen] = useState(false);
  const [seed, setSeed] = useState<Seed | undefined>();
  const allowed = Boolean(ai?.enabled && ai.can_chat);

  useEffect(() => {
    if (!allowed) return;
    const onKey = (e: globalThis.KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((o) => !o);
      }
    };
    const onAsk = (e: Event) => { setSeed((e as CustomEvent<Seed>).detail); setOpen(true); };
    document.addEventListener("keydown", onKey);
    window.addEventListener("ftp:ask", onAsk);
    return () => { document.removeEventListener("keydown", onKey); window.removeEventListener("ftp:ask", onAsk); };
  }, [allowed]);

  if (!allowed) return null;
  const mac = /Mac|iPhone|iPad/.test(navigator.platform);
  const narrow = window.matchMedia("(max-width: 700px)").matches;
  return (
    <>
      <button type="button" onClick={() => setOpen(true)} className="btn btn-secondary"
              title={`Ask a question about your book (${mac ? "⌘" : "Ctrl+"}K)`} aria-haspopup="dialog"
              style={{ display: "inline-flex", alignItems: "center", gap: 6, height: 32, padding: "0 10px",
                       borderRadius: "var(--radius-sm)", fontSize: "var(--fs-sm)", fontWeight: 600 }}>
        <Icon name="sparkle" size={15} />
        {!narrow && <span>Ask</span>}
        {!narrow && <kbd style={{ fontSize: 10, color: "var(--text-muted)", border: "1px solid var(--border)",
                      borderRadius: 4, padding: "0 4px", fontFamily: "inherit" }}>{mac ? "⌘K" : "Ctrl K"}</kbd>}
      </button>
      {open && <AskDrawer seed={seed} onClose={() => { setOpen(false); setSeed(undefined); }} />}
    </>
  );
}

/** The page the drawer was opened on, in words: what "this" means. */
function useContextLine() {
  const { filters, divisions, districts, branches, products } = useApp();
  const page = currentView();
  const extra = pageExtra();
  const bits: string[] = [];
  if (page === "coach" && extra.branchLabel) bits.push(extra.branchLabel);
  if (page === "scenario" && extra.scenarioLabel) bits.push(extra.scenarioLabel);
  if (page === "market" && extra.marketLabel) bits.push(extra.marketLabel);
  const f = filters as Record<string, unknown>;
  if (f.date_from && f.date_to) bits.push(`${shortDate(String(f.date_from))}–${shortDate(String(f.date_to))}`);
  if (f.division_id) bits.push(`${divisions.find((d) => d.id === f.division_id)?.name ?? "one"} division`);
  if (f.district_id) bits.push(`${districts.find((d) => d.id === f.district_id)?.name ?? "one"} district`);
  const bids = (f.branch_id as number[] | undefined) ?? [];
  if (bids.length) {
    const names = bids.map((id) => branches.find((b) => b.id === id)?.branch_name ?? String(id));
    bits.push(names.length > 2 ? `${names.slice(0, 2).join(", ")} +${names.length - 2}` : names.join(", "));
  }
  const pcs = (f.product_code as string[] | undefined) ?? [];
  if (pcs.length) {
    const names = pcs.map((c) => products.find((p) => p.product_code === c)?.short_name ?? c);
    bits.push(names.length > 2 ? `${names.slice(0, 2).join(", ")} +${names.length - 2}` : names.join(", "));
  }
  if (f.side) bits.push(f.side === "ASSET" ? "loans" : "deposits");
  if (f.branch_category) bits.push(String(f.branch_category).replace("_", "-").toLowerCase() + " branches");
  return { page, label: PAGE_LABELS[page] ?? page, bits, filters: f, branch: extra.branch,
           scenario: page === "scenario" ? extra.scenario : undefined };
}

function AskDrawer({ seed, onClose }: { seed?: Seed; onClose: () => void }) {
  const ctx = useContextLine();
  const [useCtx, setUseCtx] = useState<boolean>(() => {
    try { return localStorage.getItem("ftp_ask_ctx") !== "0"; } catch { return true; }
  });
  useEffect(() => { try { localStorage.setItem("ftp_ask_ctx", useCtx ? "1" : "0"); } catch { /* private mode */ } },
            [useCtx]);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [conv, setConv] = useState<string | null>(null);
  const [text, setText] = useState(seed?.preset || seed?.send ? "" : seed?.question ?? "");
  const seedSent = useRef(false);
  const [lang, setLang] = useState<"en" | "bn">(() =>
    (localStorage.getItem("ftp_ask_lang") as "en" | "bn") || "en");
  const [busy, setBusy] = useState(false);
  const [sugg, setSugg] = useState<string[]>([]);
  const [recent, setRecent] = useState<{ id: string; title: string }[]>([]);
  const input = useRef<HTMLTextAreaElement>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const abort = useRef<AbortController | null>(null);

  useEffect(() => {
    input.current?.focus();
    askApi.suggestions(currentView()).then((r) => setSugg(r.items)).catch(() => {});
    askApi.conversations().then((r) => setRecent(r.items.slice(0, 6))).catch(() => {});
    const onKey = (e: globalThis.KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
      abort.current?.abort();
    };
  }, [onClose]);

  useEffect(() => { try { localStorage.setItem("ftp_ask_lang", lang); } catch { /* private mode */ } }, [lang]);
  useEffect(() => { scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" }); },
            [turns]);

  const patch = (fn: (t: Turn) => Turn) =>
    setTurns((ts) => ts.map((t, i) => (i === ts.length - 1 ? fn(t) : t)));

  const send = async (q: string, preset?: AskPreset) => {
    q = q.trim();
    if (!q || busy) return;
    setText(""); setBusy(true);
    setTurns((ts) => [...ts, { question: q, status: "Sending" }]);
    abort.current = new AbortController();
    try {
      // Read the page at the moment of asking: a slider moved since opening counts.
      const now = pageExtra();
      const context = useCtx ? { page: ctx.page, filters: ctx.filters, branch: ctx.branch,
                                 scenario: ctx.page === "scenario" ? now.scenario : undefined,
                                 market: ctx.page === "market" ? now.market : undefined } : undefined;
      await askApi.ask({ question: q, conversation_id: conv, lang, preset, context }, (e) => {
        switch (e.type) {
          case "start": setConv(e.conversation_id); patch((t) => ({ ...t, sent: e.sent })); break;
          case "status": patch((t) => ({ ...t, status: e.text })); break;
          case "step": patch((t) => ({ ...t, steps: [...(t.steps ?? []),
            { n: e.n, tool: e.tool, title: e.title, thought: e.thought }] })); break;
          case "step_refused": patch((t) => ({ ...t, steps: (t.steps ?? []).map((x) =>
            x.n === e.n ? { ...x, refused: e.text } : x) })); break;
          case "result": patch((t) => ({
            ...t, result: t.result ?? e.result,
            steps: e.step ? (t.steps ?? []).map((x) => (x.n === e.step ? { ...x, result: e.result } : x))
              : t.steps })); break;
          case "followups": patch((t) => ({ ...t, followups: e.items })); break;
          case "answer": patch((t) => ({ ...t, answer: e, fresh: true })); break;
          case "clarify": case "refused": case "error":
            patch((t) => ({ ...t, stop: { kind: e.type, text: e.text } })); break;
          case "done": patch((t) => ({ ...t, done: true, status: undefined, messageId: e.message_id,
                                       pinnable: e.pinnable })); break;
        }
      }, abort.current.signal);
    } catch (err) {
      if ((err as Error).name !== "AbortError")
        patch((t) => ({ ...t, stop: { kind: "error", text: (err as Error).message } }));
    } finally {
      // A stream that ends with neither an answer nor a reason (the server
      // restarted, the network dropped) must not leave a silent card.
      patch((t) => ({
        ...t, done: true, status: undefined,
        stop: t.stop ?? (!t.answer && !t.messageId
          ? { kind: "error", text: "The answer was interrupted before it was written (the connection "
                                   + "closed). Ask again; the lookups are quick to repeat." }
          : undefined),
      }));
      setBusy(false);
      input.current?.focus();
    }
  };

  // A "Why?" button's question goes straight out -- one tick later. In
  // development React mounts, unmounts and remounts once; the unmount aborts
  // any request in flight, so sending on the first mount lost the answer.
  // The timer is cleared with that first mount and set again by the second.
  useEffect(() => {
    if (!(seed?.preset || seed?.send) || !seed.question) return;
    const t = setTimeout(() => {
      if (seedSent.current) return;
      seedSent.current = true;
      send(seed.question!, seed.preset);
    }, 0);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const load = async (id: string) => {
    const c = await askApi.conversation(id);
    setConv(c.id);
    setTurns(c.messages.map((m: StoredMessage) => ({
      question: m.question, result: m.result ?? undefined, done: true, messageId: m.id,
      steps: m.result?.steps && m.result.steps.length > 1
        ? m.result.steps.map((x, n) => ({ n: n + 1, tool: x.tool, title: x.result.title, thought: "",
                                          result: x.result })) : undefined,
      pinnable: m.pinnable,
      answer: m.answer != null && m.status === "ok"
        ? { type: "answer", text: m.answer, grounded: m.grounded, unverified: m.unverified,
            provider: m.provider ?? undefined, model: m.model ?? undefined, explain: !m.result, sent: "" }
        : undefined,
      stop: m.status !== "ok" && m.answer
        ? { kind: m.status === "clarify" ? "clarify" : m.status === "refused" ? "refused" : "error",
            text: m.answer } : undefined,
    })));
  };

  const pin = async (i: number) => {
    const t = turns[i];
    if (!t.messageId) return;
    try {
      await askApi.pin(t.messageId);
      setTurns((ts) => ts.map((x, n) => (n === i ? { ...x, pinned: true } : x)));
      window.dispatchEvent(new Event("ftp:pins"));
    } catch (e) {
      setTurns((ts) => ts.map((x, n) => (n === i ? { ...x, pinError: (e as Error).message } : x)));
    }
  };

  const submit = (e: FormEvent) => { e.preventDefault(); send(text); };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(text); }
  };

  return (
    <>
      <div onClick={onClose} aria-hidden style={{ position: "fixed", inset: 0, zIndex: 88,
                                                  background: "color-mix(in srgb, var(--scrim) 45%, transparent)" }} />
      <section role="dialog" aria-modal="true" aria-label="Ask FTP" style={{
        position: "fixed", top: 0, right: 0, bottom: 0, zIndex: 90, width: "min(620px, 100vw)",
        display: "flex", flexDirection: "column", background: "var(--surface-1)",
        borderLeft: "1px solid var(--border)", boxShadow: "0 12px 40px rgba(16,24,40,.22)",
        paddingTop: "env(safe-area-inset-top, 0px)",
      }}>
        <header style={{ display: "flex", alignItems: "center", gap: 8, padding: "12px 14px",
                         borderBottom: "1px solid var(--border)" }}>
          <Icon name="sparkle" />
          <b style={{ flex: 1, fontSize: "var(--fs-md)" }}>Ask FTP</b>
          <div style={{ display: "flex", gap: 2 }} role="group" aria-label="Answer language">
            <MiniButton active={lang === "en"} onClick={() => setLang("en")}>EN</MiniButton>
            <MiniButton active={lang === "bn"} onClick={() => setLang("bn")}>বাংলা</MiniButton>
          </div>
          {turns.length > 0 && <MiniButton onClick={() => { setTurns([]); setConv(null); }}
                                           disabled={busy}>New</MiniButton>}
          <IconButton icon="close" label="Close" onClick={onClose} />
        </header>

        <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "7px 14px",
                      borderBottom: "1px solid var(--border)", fontSize: "var(--fs-sm)",
                      color: useCtx ? "var(--text-secondary)" : "var(--text-muted)" }}>
          <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                title="Questions like “why is this low?” are read in the light of this page and its filters">
            {useCtx
              ? <>Asking about: <b style={{ color: "var(--text-primary)" }}>{ctx.label}</b>
                  {ctx.bits.length > 0 && <> · {ctx.bits.join(" · ")}</>}</>
              : <>Page context off: questions are answered for everything you can see</>}
          </span>
          <MiniButton active={useCtx} onClick={() => setUseCtx((v) => !v)}
                      title="Use this page and its filters as the context of questions">
            {useCtx ? "On" : "Off"}</MiniButton>
        </div>

        <div ref={scroller} style={{ flex: 1, overflowY: "auto", padding: 14, display: "flex",
                                     flexDirection: "column", gap: 16 }}>
          {turns.length === 0 && (
            <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
              <p style={{ margin: 0, color: "var(--text-secondary)", lineHeight: 1.55 }}>
                Ask about your book or the market in plain words. The answer comes from the
                platform's own figures, for the part of the bank you can see, with the chart and
                table it was read from.
              </p>
              {sugg.length > 0 && (
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                  {sugg.map((s) => (
                    <button key={s} type="button" onClick={() => send(s)} className="btn btn-secondary"
                            style={{ padding: "6px 10px", borderRadius: 999, fontSize: "var(--fs-sm)",
                                     textAlign: "left", lineHeight: 1.35 }}>{s}</button>))}
                </div>)}
              {recent.length > 0 && (
                <div>
                  <div style={{ ...muted, fontWeight: 600, marginBottom: 4 }}>Recent</div>
                  {recent.map((c) => (
                    <button key={c.id} type="button" onClick={() => load(c.id)} style={{
                      all: "unset", cursor: "pointer", display: "block", padding: "5px 0",
                      color: "var(--accent)", fontSize: "var(--fs-base)" }}>{c.title}</button>))}
                </div>)}
            </div>)}
          {turns.map((t, i) => <TurnView key={i} t={t} onPin={() => pin(i)}
                                         onAsk={i === turns.length - 1 && !busy ? (q) => send(q) : undefined} />)}
        </div>

        <form onSubmit={submit} style={{ display: "flex", gap: 8, padding: 12, alignItems: "flex-end",
                                         borderTop: "1px solid var(--border)",
                                         paddingBottom: "max(12px, env(safe-area-inset-bottom))" }}>
          <textarea ref={input} value={text} onChange={(e) => setText(e.target.value)} onKeyDown={onKey}
                    rows={Math.min(4, Math.max(1, text.split("\n").length))} maxLength={500}
                    placeholder={lang === "bn" ? "প্রশ্ন লিখুন… যেমন: গত সপ্তাহে কোন শাখা সবচেয়ে বেশি FTP মুনাফা করেছে?"
                      : "e.g. Which branches' cost of deposits rose this month?"}
                    aria-label="Your question"
                    style={{ flex: 1, resize: "none", font: "inherit", padding: "8px 10px", minHeight: 38 }} />
          <Button type="submit" variant="primary" disabled={busy || !text.trim()}>
            {busy ? "…" : "Ask"}</Button>
        </form>
      </section>
    </>
  );
}

const TOOL_WORDS: Record<string, string> = {
  compare: "Compared", trend: "Traced day by day", why: "Split the change", market: "Read the market",
  benchmarks: "Checked benchmarks", insights: "Read the findings", forecast: "Forecast the book",
  market_forecast: "Forecast market rates", policy_outlook: "Read the policy signals",
  scenario: "Ran a what-if", peer_compare: "Compared with other banks",
};

/** The lookups behind a multi-step answer, in order, each openable. */
function Steps({ steps, live }: { steps: Step[]; live: boolean }) {
  const [open, setOpen] = useState<number | null>(null);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <div style={{ ...muted, fontWeight: 700, fontSize: "var(--fs-xs)", letterSpacing: ".06em",
                    textTransform: "uppercase" }}>How I worked it out</div>
      {steps.map((x) => (
        <div key={x.n} style={{ border: "1px solid var(--border)", borderRadius: 8,
                                background: "var(--surface-2)" }}>
          <button type="button" onClick={() => setOpen(open === x.n ? null : x.n)} disabled={!x.result}
                  style={{ all: "unset", cursor: x.result ? "pointer" : "default", display: "flex", gap: 10,
                           alignItems: "flex-start", padding: "8px 10px", width: "calc(100% - 20px)" }}>
            <span style={{ flexShrink: 0, width: 20, height: 20, borderRadius: 10, display: "grid",
                           placeItems: "center", fontSize: 11, fontWeight: 700,
                           background: x.refused ? "var(--status-warning)" : x.result ? "var(--accent)" : "var(--border)",
                           color: "var(--surface-1)" }}>{x.result || x.refused ? x.n : "…"}</span>
            <span style={{ flex: 1, minWidth: 0 }}>
              <span style={{ fontWeight: 600, fontSize: "var(--fs-sm)" }}>
                {TOOL_WORDS[x.tool] ?? x.tool}{x.title ? `: ${x.title}` : ""}</span>
              {x.thought && <span style={{ ...muted, display: "block" }}>{x.thought}</span>}
              {x.refused && <span style={{ ...muted, display: "block" }}>Not available: {x.refused}</span>}
            </span>
            {x.result && <span style={{ ...muted, flexShrink: 0 }}>{open === x.n ? "Hide" : "Show"}</span>}
          </button>
          {open === x.n && x.result && (
            <div style={{ padding: "0 10px 10px" }}><AnswerView r={x.result} /></div>)}
        </div>))}
      {live && <div style={{ ...muted, display: "flex", gap: 6, alignItems: "center" }}>
        <Icon name="sparkle" size={12} />thinking…</div>}
    </div>
  );
}

/** Reveal a fresh answer a few words at a time, as if written. */
function useReveal(text: string, on: boolean) {
  const [n, setN] = useState(on ? 0 : text.length);
  useEffect(() => {
    if (!on) { setN(text.length); return; }
    setN(0);
    const step = Math.max(3, Math.ceil(text.length / 70));
    const id = window.setInterval(() => setN((x) => {
      if (x >= text.length) { window.clearInterval(id); return x; }
      return Math.min(text.length, x + step);
    }), 18);
    return () => window.clearInterval(id);
  }, [text, on]);
  return text.slice(0, n);
}

function Revealed({ text, fresh }: { text: string; fresh?: boolean }) {
  const shown = useReveal(text, Boolean(fresh));
  return <Narrative text={shown} />;
}

function TurnView({ t, onPin, onAsk }: { t: Turn; onPin: () => void; onAsk?: (q: string) => void }) {
  const a = t.answer;
  const multi = (t.steps?.length ?? 0) > 1 || (!t.done && (t.steps?.length ?? 0) > 0);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ alignSelf: "flex-end", maxWidth: "85%", background: "var(--accent-soft)",
                    color: "var(--text-primary)", padding: "8px 12px", borderRadius: "12px 12px 4px 12px",
                    fontSize: "var(--fs-base)", lineHeight: 1.45 }}>{t.question}</div>
      <div style={{ border: "1px solid var(--border)", borderRadius: 10, padding: 12,
                    display: "flex", flexDirection: "column", gap: 10 }}>
        {t.result && !multi && (
          <div>
            <div style={{ fontWeight: 650, color: "var(--text-primary)" }}>{t.result.title}</div>
            <div style={muted}>{t.result.description}</div>
          </div>)}
        {!multi && t.result?.facts && t.result.facts.length > 0 && (
          <div style={{ padding: "8px 10px", borderRadius: 8, background: "var(--surface-2)",
                        border: "1px solid var(--border)" }}>
            <div style={{ fontSize: "var(--fs-xs)", fontWeight: 700, letterSpacing: ".06em",
                          textTransform: "uppercase", color: "var(--text-muted)", marginBottom: 3 }}>
              What the figures show</div>
            {t.result.facts.map((f) => (
              <div key={f} className="tnum" style={{ fontSize: "var(--fs-base)", color: "var(--text-primary)",
                                                      lineHeight: 1.5 }}>{f}</div>))}
          </div>)}
        {a?.conflicts && a.conflicts.length > 0 && (
          <p style={{ margin: 0, fontSize: "var(--fs-sm)", lineHeight: 1.5 }}>
            <Pill tone="critical">Contradicts the figures</Pill>{" "}
            The wording below says {a.conflicts.join(", ")} moved the other way from the data.
            Rely on “What the figures show” and the table.
          </p>)}
        {a?.text && <Revealed text={a.text} fresh={t.fresh} />}
        {multi ? <Steps steps={t.steps!} live={!t.done && !a} />
          : t.result && <AnswerView r={t.result} />}
        {t.done && onAsk && t.followups && t.followups.length > 0 && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {t.followups.map((q) => (
              <button key={q} type="button" onClick={() => onAsk(q)} className="btn btn-secondary"
                      style={{ padding: "5px 10px", borderRadius: 999, fontSize: "var(--fs-sm)",
                               textAlign: "left", lineHeight: 1.35 }}>{q} →</button>))}
          </div>)}
        {t.stop && (
          <p style={{ margin: 0, lineHeight: 1.5 }}>
            <Pill tone={t.stop.kind === "clarify" ? "info" : t.stop.kind === "refused" ? "warning" : "critical"}>
              {t.stop.kind === "clarify" ? "Question" : t.stop.kind === "refused" ? "Not answered" : "Problem"}
            </Pill>{" "}{t.stop.text}
          </p>)}
        {a?.note && <p style={{ ...muted, margin: 0 }}><Pill tone="warning">No write-up</Pill> {a.note}</p>}
        {t.done && onAsk && (t.stop?.kind === "error" || a?.note) && (
          <div><MiniButton icon="refresh" onClick={() => onAsk(t.question)}>Ask again</MiniButton></div>)}
        {t.status && !t.done && (
          <div style={{ ...muted, display: "flex", alignItems: "center", gap: 8 }}>
            <Icon name="sparkle" size={13} />{t.status}…</div>)}
        {t.done && (a || t.result) && (
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", ...muted }}>
            {a?.explain ? <Pill tone="neutral">General explanation, not from your data</Pill>
              : a?.conflicts?.length ? null
              : a?.grounded ? <Pill tone="good">Numbers checked against the result</Pill>
              : a?.grounded === false
                ? <Pill tone="warning">{`Not in the result: ${a.unverified.join(", ")}`}</Pill>
                : null}
            {a?.truncated && <Pill tone="warning">Cut short by the provider: ask again</Pill>}
            {a?.provider && <span>{a.provider} · {a.model}</span>}
            <span style={{ flex: 1 }} />
            {t.pinnable && (
              <MiniButton icon="pin" active={t.pinned} disabled={t.pinned} onClick={onPin}
                          title="Keep this as a tile on the Intelligence page; it refreshes with every upload">
                {t.pinned ? "Pinned" : "Pin"}</MiniButton>)}
          </div>)}
        {t.pinError && <p style={{ ...muted, margin: 0 }}>{t.pinError}</p>}
        {t.done && t.sent !== undefined && (
          <details>
            <summary style={{ cursor: "pointer", ...muted }}>What was sent to AI</summary>
            <pre style={{ whiteSpace: "pre-wrap", fontSize: "var(--fs-xs)", background: "var(--surface-2)",
                          padding: 8, borderRadius: 6, margin: "6px 0 0", maxHeight: 220, overflow: "auto" }}>
              {`Question: ${t.sent}${a?.sent ? `\n\n${a.sent}` : ""}`}</pre>
          </details>)}
      </div>
    </div>
  );
}

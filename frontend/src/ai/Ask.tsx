import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from "react";
import { Icon } from "../components/icons";
import { Button, IconButton, MiniButton, Pill } from "../components/ui";
import { useApp } from "../state";
import { AskEvent, askApi, AskResult, StoredMessage } from "./api";
import AnswerView from "./AnswerView";
import { Narrative } from "./Brief";

/** Open Ask FTP from anywhere, optionally with a question ready to send. */
export function openAsk(question?: string) {
  window.dispatchEvent(new CustomEvent("ftp:ask", { detail: { question } }));
}

interface Turn {
  question: string;
  sent?: string;
  status?: string;
  result?: AskResult;
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
  const [seed, setSeed] = useState<string | undefined>();
  const allowed = Boolean(ai?.enabled && ai.can_chat);

  useEffect(() => {
    if (!allowed) return;
    const onKey = (e: globalThis.KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((o) => !o);
      }
    };
    const onAsk = (e: Event) => { setSeed((e as CustomEvent).detail?.question); setOpen(true); };
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

function AskDrawer({ seed, onClose }: { seed?: string; onClose: () => void }) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [conv, setConv] = useState<string | null>(null);
  const [text, setText] = useState(seed ?? "");
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
    askApi.suggestions().then((r) => setSugg(r.items)).catch(() => {});
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

  const send = async (q: string) => {
    q = q.trim();
    if (!q || busy) return;
    setText(""); setBusy(true);
    setTurns((ts) => [...ts, { question: q, status: "Sending" }]);
    abort.current = new AbortController();
    try {
      await askApi.ask({ question: q, conversation_id: conv, lang }, (e) => {
        switch (e.type) {
          case "start": setConv(e.conversation_id); patch((t) => ({ ...t, sent: e.sent })); break;
          case "status": patch((t) => ({ ...t, status: e.text })); break;
          case "result": patch((t) => ({ ...t, result: e.result })); break;
          case "answer": patch((t) => ({ ...t, answer: e })); break;
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
      patch((t) => ({ ...t, done: true, status: undefined }));
      setBusy(false);
      input.current?.focus();
    }
  };

  const load = async (id: string) => {
    const c = await askApi.conversation(id);
    setConv(c.id);
    setTurns(c.messages.map((m: StoredMessage) => ({
      question: m.question, result: m.result ?? undefined, done: true, messageId: m.id,
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
          {turns.map((t, i) => <TurnView key={i} t={t} onPin={() => pin(i)} />)}
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

function TurnView({ t, onPin }: { t: Turn; onPin: () => void }) {
  const a = t.answer;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ alignSelf: "flex-end", maxWidth: "85%", background: "var(--accent-soft)",
                    color: "var(--text-primary)", padding: "8px 12px", borderRadius: "12px 12px 4px 12px",
                    fontSize: "var(--fs-base)", lineHeight: 1.45 }}>{t.question}</div>
      <div style={{ border: "1px solid var(--border)", borderRadius: 10, padding: 12,
                    display: "flex", flexDirection: "column", gap: 10 }}>
        {t.result && (
          <div>
            <div style={{ fontWeight: 650, color: "var(--text-primary)" }}>{t.result.title}</div>
            <div style={muted}>{t.result.description}</div>
          </div>)}
        {a?.text && <Narrative text={a.text} />}
        {t.result && <AnswerView r={t.result} />}
        {t.stop && (
          <p style={{ margin: 0, lineHeight: 1.5 }}>
            <Pill tone={t.stop.kind === "clarify" ? "info" : t.stop.kind === "refused" ? "warning" : "critical"}>
              {t.stop.kind === "clarify" ? "Question" : t.stop.kind === "refused" ? "Not answered" : "Problem"}
            </Pill>{" "}{t.stop.text}
          </p>)}
        {a?.note && <p style={{ ...muted, margin: 0 }}><Pill tone="warning">No write-up</Pill> {a.note}</p>}
        {t.status && !t.done && (
          <div style={{ ...muted, display: "flex", alignItems: "center", gap: 8 }}>
            <Icon name="sparkle" size={13} />{t.status}…</div>)}
        {t.done && (a || t.result) && (
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", ...muted }}>
            {a?.explain ? <Pill tone="neutral">General explanation, not from your data</Pill>
              : a?.grounded ? <Pill tone="good">Numbers checked against the result</Pill>
              : a?.grounded === false
                ? <Pill tone="warning">{`Not in the result: ${a.unverified.join(", ")}`}</Pill>
                : null}
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

import { useEffect, useRef, useState } from "react";
import { Icon } from "../components/icons";
import { MiniButton } from "../components/ui";
import { useApp } from "../state";
import { Insight, insightApi } from "./api";
import { money, SeverityPill } from "./Insights";

/** The masthead bell: serious and critical insights not yet read.
 *  The count rides the existing status poll; the list loads on open. */
export default function Bell() {
  const { ai, refreshAi } = useApp();
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<Insight[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const unread = ai?.unread ?? 0;

  useEffect(() => {
    if (!open) return;
    let alive = true;
    insightApi.list().then((r) => {
      if (!alive) return;
      const urgent = r.items.filter((i) => i.severity === "critical" || i.severity === "serious");
      // Unread first, then the rest of what is still open.
      setItems([...urgent.filter((i) => !i.read), ...urgent.filter((i) => i.read)].slice(0, 8));
    }).catch((e) => alive && setError((e as Error).message));
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    const onClick = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onClick);
    return () => {
      alive = false;
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onClick);
    };
  }, [open]);

  const go = (i: Insight) => {
    setOpen(false);
    if (!i.read) insightApi.read([i.id]).then(refreshAi).catch(() => {});
    location.hash = `#/intel?focus=${i.id}`;
  };
  const readAll = async () => {
    await insightApi.read("all").catch(() => {});
    setItems((xs) => xs?.map((x) => ({ ...x, read: true })) ?? xs);
    refreshAi();
  };

  if (!ai?.enabled || !ai.can_view) return null;

  const label = unread ? `${unread} new alert${unread === 1 ? "" : "s"}` : "Alerts";
  return (
    <div ref={box} style={{ position: "relative" }}>
      <button type="button" onClick={() => setOpen((o) => !o)} aria-label={label} title={label}
              aria-expanded={open} aria-haspopup="dialog" className={`btn ${open ? "btn-active" : "btn-ghost"}`}
              style={{ display: "inline-grid", placeItems: "center", width: 32, height: 32, padding: 0,
                       borderRadius: "var(--radius-sm)", position: "relative" }}>
        <Icon name="bell" />
        {unread > 0 && (
          <span className="tnum" aria-hidden style={{
            position: "absolute", top: 1, right: 0, minWidth: 16, height: 16, padding: "0 4px",
            borderRadius: 999, background: "var(--status-critical)", color: "#fff",
            fontSize: 10, fontWeight: 700, lineHeight: "16px", textAlign: "center",
            boxShadow: "0 0 0 2px var(--surface-1)",
          }}>{unread > 9 ? "9+" : unread}</span>)}
      </button>
      {open && (
        <div role="dialog" aria-label="Alerts" style={{
          position: "absolute", right: 0, top: 40, zIndex: 80, width: "min(380px, calc(100vw - 24px))",
          background: "var(--surface-1)", border: "1px solid var(--border)", borderRadius: "var(--radius-sm)",
          boxShadow: "var(--shadow-md)", overflow: "hidden",
        }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "10px 12px",
                        borderBottom: "1px solid var(--border)" }}>
            <b style={{ flex: 1, fontSize: "var(--fs-base)" }}>Needs a decision</b>
            {unread > 0 && <MiniButton onClick={readAll}>Mark all read</MiniButton>}
          </div>
          <div style={{ maxHeight: 420, overflowY: "auto" }}>
            {error && <p style={{ margin: 12, color: "var(--text-muted)" }}>{error}</p>}
            {items === null && !error && <p style={{ margin: 12, color: "var(--text-muted)" }}>Loading…</p>}
            {items?.length === 0 && (
              <p style={{ margin: 12, fontSize: "var(--fs-base)", color: "var(--text-muted)" }}>
                Nothing serious is open. Lesser items are on the Intelligence page.
              </p>)}
            {items?.map((i) => (
              <button key={i.id} type="button" onClick={() => go(i)} style={{
                all: "unset", cursor: "pointer", display: "block", width: "100%", boxSizing: "border-box",
                padding: "10px 12px", borderBottom: "1px solid var(--border)",
                background: i.read ? undefined : "color-mix(in srgb, var(--accent) 5%, transparent)",
              }}>
                <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 3 }}>
                  <SeverityPill s={i.severity} />
                  {money(i) && <span className="tnum" style={{ fontSize: "var(--fs-sm)",
                                                            color: "var(--text-secondary)" }}>{money(i)}</span>}
                </div>
                <div style={{ fontSize: "var(--fs-base)", fontWeight: i.read ? 500 : 650,
                              color: "var(--text-primary)", lineHeight: 1.4 }}>{i.title}</div>
              </button>))}
          </div>
          <a href="#/intel" onClick={() => setOpen(false)} style={{
            display: "block", padding: "9px 12px", fontSize: "var(--fs-sm)", textAlign: "center" }}>
            Open Intelligence
          </a>
        </div>)}
    </div>
  );
}

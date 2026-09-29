import { CSSProperties, ReactNode, forwardRef, useEffect, useLayoutEffect, useRef, useState } from "react";
import { compact, money, n, pct, signed, toCsv } from "../format";
import { ExpandedHeight } from "./fullscreen";
import { Icon, type IconName } from "./icons";

// --------------------------------------------------------------------------
// Card
// --------------------------------------------------------------------------

export function Card({
  title, subtitle, children, actions, footnote, pad = true, expandable = false,
}: {
  title?: ReactNode; subtitle?: ReactNode; children: ReactNode;
  actions?: ReactNode; footnote?: ReactNode; pad?: boolean;
  /**
   * Adds the full-screen toggle in the bottom-right of the body. For cards
   * whose body is a chart: a dense curve is often unreadable at card size.
   */
  expandable?: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const [roomFor, setRoomFor] = useState<number | null>(null);
  const sectionRef = useRef<HTMLElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const everExpanded = useRef(false);

  // Escape closes, the page behind must not scroll under the overlay, and the
  // toggle takes focus so the keyboard lands somewhere useful. All of it is
  // undone on collapse *and* on unmount -- a card removed while expanded (a
  // filter change that empties the view, say) must not leave the body locked.
  useEffect(() => {
    if (!expanded) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { setExpanded(false); return; }
      // The overlay covers the page, so Tab must not walk off into the
      // controls behind it -- they are hidden but still focusable, and a
      // keyboard user would be typing into something they cannot see.
      if (e.key !== "Tab") return;
      const root = sectionRef.current;
      if (!root) return;
      const items = Array.from(root.querySelectorAll<HTMLElement>(
        'button, [href], select, input, textarea, [tabindex]:not([tabindex="-1"])',
      )).filter((el) => !el.hasAttribute("disabled") && el.offsetParent !== null);
      if (!items.length) return;
      const first = items[0], last = items[items.length - 1];
      const active = document.activeElement as HTMLElement | null;
      const outside = !active || !root.contains(active);
      if (e.shiftKey && (outside || active === first)) {
        e.preventDefault(); last.focus();
      } else if (!e.shiftKey && (outside || active === last)) {
        e.preventDefault(); first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    toggleRef.current?.focus();
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [expanded]);

  // Focus returns to the toggle after collapsing -- it is the same button, so
  // the eye does not have to hunt for where it went. Never on first render.
  useEffect(() => {
    if (expanded) { everExpanded.current = true; return; }
    if (everExpanded.current) toggleRef.current?.focus();
  }, [expanded]);

  // Measure the room the body actually has, and hand it to the chart. Read
  // before paint so the chart is never drawn once at the wrong size and then
  // corrected, which reads as a flicker.
  useLayoutEffect(() => {
    const el = bodyRef.current;
    if (!expanded || !el) { setRoomFor(null); return; }
    const measure = () => setRoomFor(el.clientHeight);
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [expanded]);

  // Every key here must be the same property the base style sets, so React
  // can diff it back on collapse. `borderWidth: 0` was not: setting the
  // longhand wiped the `border` shorthand's serialisation, and removing it
  // again left the width at its initial `medium` (3px). The card came back
  // two pixels thicker on each side and every chart in it redrew 4px
  // narrower -- which is what "returns to a distorted position" was.
  const overlay: CSSProperties = expanded
    ? { position: "fixed", inset: 0, zIndex: 100, borderRadius: 0,
        boxShadow: "none", border: "none" }
    : {};

  return (
    <section
      ref={sectionRef}
      style={{
        background: "var(--surface-1)", border: "1px solid var(--border)",
        borderRadius: "var(--radius)", boxShadow: "var(--shadow-sm)",
        display: "flex", flexDirection: "column", minWidth: 0,
        ...overlay,
      }}
      {...(expanded
        ? { role: "dialog", "aria-modal": true,
            "aria-label": typeof title === "string" ? title : "Chart, full screen" }
        : {})}
    >
      {(title || actions) && (
        <header style={{
          display: "flex", alignItems: "flex-start", justifyContent: "space-between",
          gap: 12, padding: expanded ? "20px 24px 0" : "16px 20px 0", flexWrap: "wrap",
        }}>
          <div style={{ minWidth: 0 }}>
            {title && <h3 style={{
              margin: 0, fontSize: expanded ? "var(--fs-lg)" : "var(--fs-md)",
              fontWeight: 600, lineHeight: 1.35, color: "var(--text-primary)",
            }}>{title}</h3>}
            {subtitle && <p style={{
              margin: "3px 0 0", fontSize: "var(--fs-sm)", color: "var(--text-muted)",
            }}>{subtitle}</p>}
          </div>
          {/* The filters ride along: a chart is not much use full screen if
              the controls that decide what it shows stay behind. */}
          {actions && <div style={{ display: "flex", gap: 6, flexShrink: 0 }}>{actions}</div>}
        </header>
      )}
      <div style={{
        padding: pad ? (expanded ? "12px 24px 18px" : "12px 16px 16px") : 0,
        flex: 1, minWidth: 0,
        ...(expandable ? { position: "relative" } : {}),
        ...(expanded ? { display: "flex", flexDirection: "column", minHeight: 0 } : {}),
      }}>
        {/* Always rendered, so expanding re-styles the chart rather than
            remounting it -- a remount would tear down the ECharts instance
            and flash. `display: contents` keeps the box out of the layout
            entirely while the card sits normally on the page. */}
        <div
          ref={bodyRef}
          style={expanded
            ? { flex: 1, minHeight: 0, overflow: "auto" }
            : { display: "contents" }}
        >
          <ExpandedHeight.Provider value={roomFor}>{children}</ExpandedHeight.Provider>
        </div>
        {expandable && (
          <FullscreenToggle ref={toggleRef} expanded={expanded}
                            onClick={() => setExpanded((v) => !v)} />
        )}
      </div>
      {footnote && (
        <footer style={{
          padding: expanded ? "10px 24px 16px" : "10px 20px 14px",
          borderTop: "1px solid var(--grid)",
          // A footnote explains the card; it never competes with it. Set in
          // the smallest step and muted so the eye reaches it last.
          fontSize: "var(--fs-xs)", color: "var(--text-muted)", lineHeight: 1.5,
        }}>{footnote}</footer>
      )}
    </section>
  );
}

/**
 * Room to leave in the bottom-right of an expandable card's body so content
 * does not end up underneath the full-screen toggle. A chart can tolerate the
 * overlap -- that corner is axis, not data -- but anything right-aligned on
 * the last row, a total most of all, disappears behind it.
 */
export const TOGGLE_GUTTER = 34;

/** The expand / exit control that sits over the bottom-right of a chart. */
const FullscreenToggle = forwardRef<HTMLButtonElement, {
  expanded: boolean; onClick: () => void;
}>(function FullscreenToggle({ expanded, onClick }, ref) {
  const [hot, setHot] = useState(false);
  return (
    <button
      ref={ref}
      type="button"
      onClick={onClick}
      onMouseEnter={() => setHot(true)}
      onMouseLeave={() => setHot(false)}
      onFocus={() => setHot(true)}
      onBlur={() => setHot(false)}
      aria-label={expanded ? "Exit full screen" : "View chart full screen"}
      title={expanded ? "Exit full screen (Esc)" : "View full screen"}
      style={{
        position: "absolute", right: expanded ? 24 : 12, bottom: expanded ? 18 : 12,
        zIndex: 2, width: 26, height: 26, padding: 0,
        display: "grid", placeItems: "center", cursor: "pointer",
        background: "var(--surface-1)", borderRadius: "var(--radius-xs)",
        border: "1px solid var(--border-strong)", boxShadow: "var(--shadow-sm)",
        color: hot ? "var(--text-primary)" : "var(--text-muted)",
        opacity: hot ? 1 : 0.75, transition: "opacity .15s ease, color .15s ease",
      }}
    >
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none"
           stroke="currentColor" strokeWidth="2.2" strokeLinecap="round"
           strokeLinejoin="round" aria-hidden="true">
        {expanded ? (
          <>
            <polyline points="4 14 10 14 10 20" />
            <polyline points="20 10 14 10 14 4" />
            <line x1="14" y1="10" x2="21" y2="3" />
            <line x1="3" y1="21" x2="10" y2="14" />
          </>
        ) : (
          <>
            <polyline points="15 3 21 3 21 9" />
            <polyline points="9 21 3 21 3 15" />
            <line x1="21" y1="3" x2="14" y2="10" />
            <line x1="3" y1="21" x2="10" y2="14" />
          </>
        )}
      </svg>
    </button>
  );
});

// --------------------------------------------------------------------------
// Stat tile -- the form for "the story is one number"
// --------------------------------------------------------------------------

export function Stat({
  label, value, delta, deltaPct, hint, spark, tone = "neutral", invertDelta = false, why,
}: {
  label: string; value: string; delta?: string | null; deltaPct?: string | null;
  hint?: string; spark?: number[]; tone?: "neutral" | "good" | "bad";
  invertDelta?: boolean;
  /** Explain this figure (the optional AI module). No button when absent. */
  why?: () => void;
}) {
  const d = delta == null ? null : n(delta);
  const improving = d == null ? null : invertDelta ? d < 0 : d > 0;
  const deltaColor = improving == null ? "var(--text-muted)"
    : improving ? "var(--delta-up)" : "var(--delta-down)";

  return (
    <div style={{
      background: "var(--surface-1)", border: "1px solid var(--border)",
      borderRadius: "var(--radius)", padding: "16px 18px",
      boxShadow: "var(--shadow-sm)", display: "flex", flexDirection: "column", gap: 6,
      minWidth: 0,
    }}>
      <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <span style={{
          fontSize: "var(--fs-xs)", fontWeight: 600, letterSpacing: ".05em",
          textTransform: "uppercase", color: "var(--text-secondary)", flex: 1, minWidth: 0,
        }}>{label}</span>
        {why && (
          <button type="button" onClick={why} className="btn btn-ghost"
                  aria-label={`Why? Explain ${label}`} title="Why did this move?"
                  style={{ display: "inline-grid", placeItems: "center", width: 24, height: 24,
                           padding: 0, borderRadius: 6, color: "var(--accent)", flexShrink: 0,
                           margin: "-4px -6px -4px 0" }}>
            <Icon name="sparkle" size={14} />
          </button>
        )}
      </span>

      {/* Proportional figures: tabular-nums reads loose at display size. */}
      <strong style={{
        // Long figures (lakh-grouped money) step down so they stay in the tile.
        fontSize: value.length > 12 ? 22 : value.length > 9 ? 24 : "var(--fs-xl)",
        fontWeight: 650, lineHeight: 1.1, letterSpacing: "-.02em",
        color: tone === "good" ? "var(--delta-up)"
             : tone === "bad" ? "var(--delta-down)" : "var(--text-primary)",
      }}>{value}</strong>

      {(d != null || hint) && (
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          {d != null && (
            <span style={{ fontSize: "var(--fs-sm)", fontWeight: 600, color: deltaColor,
                           display: "inline-flex", alignItems: "center", gap: 3 }}>
              {/* Direction is carried by the arrow glyph and the sign, not by
                  colour alone. */}
              <span aria-hidden>{improving ? "▲" : "▼"}</span>
              {signed(delta)}
              {deltaPct != null && <span style={{ opacity: .85 }}>({pct(deltaPct, 1)})</span>}
            </span>
          )}
          {hint && <span style={{ fontSize: "var(--fs-sm)", color: "var(--text-muted)" }}>{hint}</span>}
        </div>
      )}

      {spark && spark.length > 1 && <Sparkline values={spark} />}
    </div>
  );
}

function Sparkline({ values }: { values: number[] }) {
  const w = 120, h = 24;
  const min = Math.min(...values), max = Math.max(...values);
  const span = max - min || 1;
  const pts = values.map((v, i) => {
    const x = (i / (values.length - 1)) * w;
    const y = h - ((v - min) / span) * (h - 4) - 2;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} style={{ marginTop: 2 }} aria-hidden>
      <polyline points={pts} fill="none" stroke="var(--series-1)" strokeWidth="2"
                strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

// --------------------------------------------------------------------------
// Table -- every chart's accessible twin
// --------------------------------------------------------------------------

export interface Col<T> {
  key: string;
  label: string;
  align?: "left" | "right";
  render?: (row: T) => ReactNode;
  value?: (row: T) => unknown;
  width?: number | string;
}

export function Table<T>({
  cols, rows, empty = "No data for this selection.", maxHeight, csvName, onRowClick,
  search, searchPlaceholder = "Search…",
}: {
  cols: Col<T>[]; rows: T[]; empty?: string; maxHeight?: number;
  csvName?: string; onRowClick?: (row: T) => void;
  /**
   * Adds a search box that filters rows as you type. Returns the text a row
   * can be found by; every word typed must appear in it, in any order, so a
   * code and part of a name can be combined ("tdr 6", "gulshan corp").
   */
  search?: (row: T) => string;
  searchPlaceholder?: string;
}) {
  const [query, setQuery] = useState("");

  if (!rows.length) {
    return <p style={{ padding: "20px 4px", color: "var(--text-muted)",
                       fontSize: "var(--fs-base)", margin: 0 }}>
      {empty}
    </p>;
  }

  const terms = query.toLowerCase().split(/\s+/).filter(Boolean);
  const shown = search && terms.length
    ? rows.filter((r) => {
        const text = search(r).toLowerCase();
        return terms.every((t) => text.includes(t));
      })
    : rows;

  return (
    <>
      {(csvName || search) && (
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10,
                      flexWrap: "wrap" }}>
          {search && (
            <div style={{ position: "relative", flex: "1 1 220px", maxWidth: 340 }}>
              <input type="search" value={query} placeholder={searchPlaceholder}
                     aria-label={searchPlaceholder}
                     onChange={(e) => setQuery(e.target.value)}
                     onKeyDown={(e) => { if (e.key === "Escape") setQuery(""); }}
                     style={{ width: "100%" }} />
            </div>
          )}
          {search && terms.length > 0 && (
            <span style={{ fontSize: "var(--fs-sm)", color: "var(--text-muted)" }}>
              {shown.length.toLocaleString("en-IN")} of {rows.length.toLocaleString("en-IN")}
            </span>
          )}
          {csvName && (
            <MiniButton icon="download" style={{ marginLeft: "auto" }} onClick={() => toCsv(
              shown.map((r) => Object.fromEntries(
                cols.map((c) => [c.label, c.value ? c.value(r) : (r as never)[c.key]]),
              )), csvName,
            )}>Export CSV</MiniButton>
          )}
        </div>
      )}
      {!shown.length && (
        <p style={{ padding: "20px 4px", color: "var(--text-muted)",
                    fontSize: "var(--fs-base)", margin: 0 }}>
          Nothing matches “{query.trim()}”.
        </p>
      )}
      {shown.length > 0 && (
      <div style={{ overflowX: "auto", maxHeight, overflowY: maxHeight ? "auto" : undefined,
                    borderRadius: "var(--radius-sm)" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "var(--fs-base)" }}>
          <thead>
            <tr>
              {cols.map((c) => (
                <th key={c.key} scope="col" style={{
                  textAlign: c.align ?? "left", padding: "9px 12px",
                  position: maxHeight ? "sticky" : undefined, top: 0, zIndex: 1,
                  background: "var(--surface-2)",
                  borderBottom: "1px solid var(--border)",
                  color: "var(--text-secondary)", fontWeight: 600, fontSize: "var(--fs-xs)",
                  letterSpacing: ".05em", textTransform: "uppercase",
                  whiteSpace: "nowrap", width: c.width,
                }}>{c.label}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.map((r, i) => (
              <tr key={i} className={onRowClick ? "clickable" : undefined}
                  onClick={onRowClick ? () => onRowClick(r) : undefined}
                  style={{
                    borderBottom: "1px solid var(--grid)",
                    cursor: onRowClick ? "pointer" : undefined,
                  }}>
                {cols.map((c) => (
                  <td key={c.key} className="tnum" style={{
                    textAlign: c.align ?? "left", padding: "10px 12px",
                    color: "var(--text-secondary)", whiteSpace: "nowrap",
                    transition: "background .1s ease",
                  }}>
                    {c.render ? c.render(r) : String((r as never)[c.key] ?? "")}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      )}
    </>
  );
}

// --------------------------------------------------------------------------
// Small pieces
// --------------------------------------------------------------------------

/** The small control: toolbar toggles, sort keys, table actions. `active`
 *  marks the selected one of a set. */
export function MiniButton({ children, onClick, active, title, disabled, style, icon }: {
  children: ReactNode; onClick?: () => void; active?: boolean; title?: string;
  disabled?: boolean; style?: React.CSSProperties; icon?: IconName;
}) {
  return (
    <button type="button" onClick={disabled ? undefined : onClick} title={title}
            disabled={disabled} aria-pressed={active}
            className={`btn ${active ? "btn-active" : "btn-secondary"}`} style={{
      display: "inline-flex", alignItems: "center", gap: 6,
      height: 28, padding: "0 10px", borderRadius: "var(--radius-xs)",
      fontSize: "var(--fs-sm)", fontWeight: active ? 600 : 500,
      whiteSpace: "nowrap", ...style,
    }}>{icon && <Icon name={icon} size={14} />}{children}</button>
  );
}

export function Button({ children, onClick, variant = "default", size = "md", disabled,
  type = "button", style, icon }: {
  children: ReactNode; onClick?: () => void;
  variant?: "default" | "primary" | "danger" | "ghost"; size?: "sm" | "md";
  disabled?: boolean; type?: "button" | "submit"; style?: React.CSSProperties;
  icon?: IconName;
}) {
  const cls = { default: "btn-secondary", primary: "btn-primary",
                danger: "btn-danger", ghost: "btn-ghost" }[variant];
  return (
    <button type={type} onClick={onClick} disabled={disabled} className={`btn ${cls}`} style={{
      display: "inline-flex", alignItems: "center", justifyContent: "center", gap: 7,
      height: size === "sm" ? 28 : 34, padding: size === "sm" ? "0 10px" : "0 14px",
      borderRadius: "var(--radius-sm)",
      fontSize: size === "sm" ? "var(--fs-sm)" : "var(--fs-base)", fontWeight: 600,
      whiteSpace: "nowrap", ...style,
    }}>{icon && <Icon name={icon} size={size === "sm" ? 14 : 16} />}{children}</button>
  );
}

/** A square, label-less control. The label goes to assistive tech and the
 *  tooltip, so the icon never has to carry the meaning alone. */
export function IconButton({ icon, label, onClick, active, style }: {
  icon: IconName; label: string; onClick?: () => void; active?: boolean;
  style?: React.CSSProperties;
}) {
  return (
    <button type="button" onClick={onClick} aria-label={label} title={label}
            aria-pressed={active}
            className={`btn ${active ? "btn-active" : "btn-ghost"}`} style={{
      display: "inline-grid", placeItems: "center", width: 32, height: 32, padding: 0,
      borderRadius: "var(--radius-sm)", flexShrink: 0, ...style,
    }}><Icon name={icon} /></button>
  );
}

/** Status pill. Always icon + label -- colour never carries the meaning alone. */
export function Pill({ tone, children }: {
  tone: "good" | "warning" | "critical" | "neutral" | "info"; children: ReactNode;
}) {
  const map = {
    good:     { c: "var(--status-good)", i: "✓" },
    warning:  { c: "var(--status-warning)", i: "△" },
    critical: { c: "var(--status-critical)", i: "✕" },
    info:     { c: "var(--accent)", i: "ℹ" },
    neutral:  { c: "var(--text-muted)", i: "·" },
  }[tone];
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 5,
      fontSize: "var(--fs-xs)", fontWeight: 600, lineHeight: "18px", color: map.c,
      background: `color-mix(in srgb, ${map.c} var(--tint), transparent)`,
      border: `1px solid color-mix(in srgb, ${map.c} 22%, transparent)`,
      borderRadius: 999, padding: "1px 9px", whiteSpace: "nowrap",
    }}>
      <span aria-hidden>{map.i}</span>{children}
    </span>
  );
}

/** Toggle between a chart and its table twin. */
export function ViewToggle({ view, setView }: {
  view: "chart" | "table"; setView: (v: "chart" | "table") => void;
}) {
  return (
    <div style={{ display: "flex", gap: 2 }}>
      <MiniButton active={view === "chart"} onClick={() => setView("chart")}>Chart</MiniButton>
      <MiniButton active={view === "table"} onClick={() => setView("table")}>Table</MiniButton>
    </div>
  );
}

export function useView() {
  return useState<"chart" | "table">("chart");
}

/** 1st, 2nd, 3rd, 4th … 11th, 12th, 13th, 21st … */
const ordinal = (n: number) => {
  const s = ["th", "st", "nd", "rd"];
  const v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]);
};

/**
 * "showing 1st … 5 branches\n· based on …" — a window picker for a bar chart
 * that shows a page of items rather than the whole list. The caller slices the
 * ranked data; this widget only chooses the 5-item window and, optionally, the
 * ranking basis the window is cut from. Stacks into up to two short lines so
 * it can ride beside the card title without making the card taller.
 */
export function RankFilter({
  count, page, onPage, rank, ranks, onRank, pageSize = 5, unit,
}: {
  count: number; page: number; onPage: (p: number) => void;
  rank?: string; ranks?: { id: string; label: string }[]; onRank?: (r: string) => void;
  pageSize?: number; unit: string;
}) {
  const chunks = Math.max(1, Math.ceil(count / pageSize));
  const safe = Math.min(Math.max(page, 0), chunks - 1);
  const select: React.CSSProperties = {
    minHeight: 26, padding: "0 6px", borderRadius: "var(--radius-xs)",
    fontSize: "var(--fs-sm)", minWidth: 0, cursor: "pointer",
  };
  const row: React.CSSProperties = {
    display: "flex", alignItems: "center", gap: 5, whiteSpace: "nowrap",
  };
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end",
                  gap: 4, fontSize: "var(--fs-xs)", color: "var(--text-muted)" }}>
      <div style={row}>
        <span>showing</span>
        <select style={select} value={safe} disabled={count === 0}
                onChange={(e) => onPage(+e.target.value)}
                aria-label={`Which ${pageSize}-item window`}>
          {Array.from({ length: chunks }, (_, i) => (
            <option key={i} value={i}>{ordinal(i + 1)}</option>
          ))}
        </select>
        <span>{pageSize} {unit}</span>
      </div>
      {ranks && rank != null && onRank && (
        <div style={row}>
          <span>based on</span>
          <select style={select} value={rank}
                  onChange={(e) => onRank(e.target.value)}
                  aria-label="Ranking basis">
            {ranks.map((r) => <option key={r.id} value={r.id}>{r.label}</option>)}
          </select>
        </div>
      )}
    </div>
  );
}

export function Empty({ title, hint, action }: {
  title: string; hint?: string; action?: ReactNode;
}) {
  return (
    <div style={{
      padding: "36px 20px", textAlign: "center", color: "var(--text-muted)",
    }}>
      <p style={{ margin: 0, fontSize: "var(--fs-md)", color: "var(--text-secondary)", fontWeight: 600 }}>
        {title}
      </p>
      {hint && <p style={{ margin: "6px 0 0", fontSize: "var(--fs-base)", maxWidth: 460,
                           marginInline: "auto", lineHeight: 1.5 }}>{hint}</p>}
      {action && <div style={{ marginTop: 14 }}>{action}</div>}
    </div>
  );
}

export function Grid({ cols, children, gap = 16 }: {
  cols: string; children: ReactNode; gap?: number;
}) {
  return <div style={{ display: "grid", gridTemplateColumns: cols, gap }}>{children}</div>;
}

export { money, compact, pct };

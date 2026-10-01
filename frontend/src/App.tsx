import { useEffect, useRef, useState } from "react";
import FilterBar from "./components/FilterBar";
import { Icon, type IconName } from "./components/icons";
import { Button, Card, IconButton, MiniButton, Pill } from "./components/ui";
import { api } from "./api";
import { AppProvider, currentView, useApp } from "./state";
import Accounts from "./views/Accounts";
import Activity from "./views/Activity";
import Admin from "./views/Admin";
import BasicOverview from "./views/BasicOverview";
import Consolidated from "./views/Consolidated";
import Analytics from "./views/Analytics";
import Daily from "./views/Daily";
import Leaders from "./views/Leaders";
import Overview from "./views/Overview";
import Rates from "./views/Rates";
import Upload from "./views/Upload";
import AiAdmin from "./ai/AiAdmin";
import Intelligence from "./ai/Intelligence";
import Bell from "./ai/Bell";
import AskLauncher from "./ai/Ask";
import Coach from "./ai/Coach";
import Outlook from "./ai/Outlook";
import Pulse from "./ai/Pulse";
import AlcoPack from "./ai/AlcoPack";
import Scenario from "./ai/Scenario";

//: Basic overview is first and is where a session lands after sign-in.
//: `currentView` defaults to the same id, so the landing page and the first
//: nav item cannot drift. `desc` is the one line under the page title;
//: `bare` pages skip the title block to give the screen to their charts.
const NAV: { id: string; label: string; group: string; icon: IconName;
             desc: string; perm?: string; bare?: boolean; ai?: boolean }[] = [
  // The landing page opens straight onto its four charts.
  { id: "basic", label: "Basic overview", group: "Analyse", icon: "dashboard", bare: true,
    desc: "Branch, product and side-by-side FTP profitability at a glance." },
  { id: "daily", label: "Daily", group: "Analyse", icon: "calendar",
    desc: "What needs attention, where the margin went, and whether the ratios moved." },
  { id: "overview", label: "Overview", group: "Analyse", icon: "pie",
    desc: "Trend, sources of margin and balance-sheet structure for the period." },
  { id: "analytics", label: "Analytics", group: "Analyse", icon: "chart",
    desc: "Variance, concentration and rate distribution across the book." },
  { id: "leaders", label: "Leaders", group: "Analyse", icon: "trophy",
    desc: "The best branches in each group, and which product leads where." },
  { id: "accounts", label: "Accounts", group: "Analyse", icon: "list",
    desc: "Where the book loses money, down to the individual account." },
  { id: "consolidated", label: "Consolidated", group: "Analyse", icon: "layers",
    desc: "Every account-day as one row, as in the Consolidated Data sheet." },
  // Shown only while the AI module is loaded and switched on.
  { id: "intel", label: "Intelligence", group: "Intelligence", icon: "globe",
    perm: "AI_VIEW", ai: true,
    desc: "This morning's brief, what needs a decision, and the market behind it." },
  { id: "pulse", label: "Bank pulse", group: "Intelligence", icon: "target",
    perm: "AI_VIEW", ai: true,
    desc: "One score for the bank's health, what it is made of, and the bank against the industry." },
  { id: "outlook", label: "Outlook", group: "Intelligence", icon: "trend",
    perm: "AI_VIEW", ai: true,
    desc: "Where the book, the market and the policy rate are heading, and how we price against other banks." },
  { id: "scenario", label: "Scenario lab", group: "Intelligence", icon: "layers",
    perm: "SCENARIO_RUN", ai: true,
    desc: "Move rates, pass-through and balances, and see bank NII, branch profit and treasury respond." },
  { id: "alco", label: "ALCO pack", group: "Intelligence", icon: "download",
    perm: "SCENARIO_RUN", ai: true,
    desc: "The month's asset-liability pack: outlook, sensitivity, pricing and decisions, ready to print." },
  { id: "coach", label: "Branch coach", group: "Intelligence", icon: "target",
    perm: "AI_VIEW", ai: true,
    desc: "Where a branch stands among its peers, and the actions worth most this week." },
  { id: "upload", label: "Upload", group: "Operate", icon: "upload", perm: "UPLOAD_VIEW",
    desc: "Load bank data files and follow each batch through processing." },
  { id: "admin", label: "Master data", group: "Operate", icon: "database",
    perm: "MASTER_BRANCH_VIEW", desc: "The branch and product masters the calculation runs on." },
  { id: "rates", label: "Rate configuration", group: "Operate", icon: "percent",
    perm: "CONFIG_RATE_VIEW", desc: "FTP rate components in force, and how they have changed." },
  { id: "activity", label: "Activity log", group: "Operate", icon: "history",
    perm: "AUDIT_VIEW", desc: "Every change made in the system, who made it and when." },
  // Exists only when the backend runs the AI module: without it no user holds
  // AI_ADMIN, so the permission filter alone hides it.
  { id: "ai", label: "AI management", group: "Operate", icon: "sparkle", perm: "AI_ADMIN",
    desc: "AI providers and keys, the system-wide switch, and exactly what may leave the bank." },
];

/** Below this width the sidebar leaves the layout and becomes a drawer. */
const DRAWER_BELOW = 1024;

function useNarrow() {
  const q = `(max-width: ${DRAWER_BELOW - 1}px)`;
  const [narrow, setNarrow] = useState(() => window.matchMedia(q).matches);
  useEffect(() => {
    const mq = window.matchMedia(q);
    const on = () => setNarrow(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, [q]);
  return narrow;
}

export default function App() {
  return (
    <AppProvider>
      <Shell />
    </AppProvider>
  );
}

function Shell() {
  const { me, ready, can, dataInfo, lastSync, refreshData, filters, ai } = useApp();
  const [view, setView] = useState(currentView());
  const narrow = useNarrow();
  // The sidebar rests as the icon rail, so the page always has its full
  // width. Opening it lays the full menu over the page rather than pushing
  // the page aside; choosing a destination puts it away again.
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [filtersOpen, setFiltersOpen] = useState(
    () => (localStorage.getItem("ftp_filters_open") ?? "1") === "1",
  );

  useEffect(() => { localStorage.setItem("ftp_filters_open", filtersOpen ? "1" : "0"); }, [filtersOpen]);

  useEffect(() => {
    const onHash = () => { setView(currentView()); setDrawerOpen(false); };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  // The open menu is modal: Esc closes it and the page behind does not scroll.
  useEffect(() => {
    if (!drawerOpen) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setDrawerOpen(false); };
    document.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [drawerOpen]);

  if (!ready) {
    return <Centered><p style={{ color: "var(--text-muted)" }}>Loading…</p></Centered>;
  }
  if (!me) return <Login />;
  // A seeded password is a shared secret until it is replaced, so the flag
  // gates the application rather than merely suggesting a change.
  if (me.must_change_password) return <ForcePasswordChange />;

  const visible = NAV.filter((n) => (!n.perm || can(n.perm)) && (!n.ai || ai?.enabled));
  const Current = { basic: BasicOverview, consolidated: Consolidated,
                    daily: Daily, overview: Overview, analytics: Analytics,
                    leaders: Leaders, accounts: Accounts, upload: Upload,
                    admin: Admin, rates: Rates, activity: Activity, ai: AiAdmin,
                    intel: ai?.enabled ? Intelligence : Daily,
                    coach: ai?.enabled ? Coach : Daily,
                    outlook: ai?.enabled ? Outlook : Daily,
                    scenario: ai?.enabled ? Scenario : Daily,
                    pulse: ai?.enabled ? Pulse : Daily,
                    alco: ai?.enabled ? AlcoPack : Daily }[view] ?? Daily;
  const page = NAV.find((n) => n.id === view) ?? NAV.find((n) => n.id === "daily")!;

  const activeFilterCount = (Object.entries(filters) as [string, unknown][]).reduce(
    (n, [, v]) => n + (Array.isArray(v) ? v.length : v ? 1 : 0), 0,
  );

  const toggleNav = () => setDrawerOpen((v) => !v);
  const closeNav = () => setDrawerOpen(false);

  return (
    <div className="canvas-art" style={{ display: "flex", minHeight: "100%" }}>
      {/* The rail holds its place in the layout; phones have no room for it. */}
      {!narrow && <Sidebar items={visible} view={view} rail />}
      {drawerOpen && (
        <>
          <div onClick={closeNav} aria-hidden className="nav-scrim" style={{
            position: "fixed", inset: 0, zIndex: 60,
            // Lighter on a desktop, where the page behind stays in view.
            background: narrow ? "var(--scrim)"
              : "color-mix(in srgb, var(--scrim) 45%, transparent)",
          }} />
          <Sidebar items={visible} view={view} rail={false} drawer onClose={closeNav} />
        </>
      )}

      <main style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
        <div className="masthead" style={{
          position: "sticky", top: "env(safe-area-inset-top, 0px)", zIndex: 30,
          borderBottom: "1px solid var(--border)",
        }}>
          <div style={{
            display: "flex", alignItems: "center", gap: narrow ? 6 : 10,
            padding: narrow ? "0 10px 0 8px" : "0 16px 0 12px", minHeight: 56,
          }}>
            <IconButton icon="menu" onClick={toggleNav}
                        label={drawerOpen ? "Close navigation" : "Open navigation"} />

            {narrow && (
              <BrandLogo height={24} />
            )}

            <div style={{ flex: 1, minWidth: 0, display: "flex", alignItems: "center" }}>
              {!filtersOpen && !narrow && <FilterBar collapsed />}
            </div>

            {/* Live state is stated, never implied: the page says how fresh it
                is rather than silently showing stale numbers. */}
            <div style={{ display: "flex", alignItems: "center", gap: 8,
                          fontSize: "var(--fs-sm)", color: "var(--text-muted)" }}>
              <Pill tone={dataInfo?.last_batch_status === "COMPLETED" ? "good" : "warning"}>
                {dataInfo?.latest_business_date
                  ? `${narrow ? "" : "Data to "}${dataInfo.latest_business_date}`
                  : "No data"}
              </Pill>
              {!narrow && lastSync && (
                <span className="tnum" title={dataInfo?.last_batch_ref
                  ? `Last batch ${dataInfo.last_batch_ref}` : undefined}>
                  checked {lastSync.toLocaleTimeString("en-GB")}
                </span>
              )}
              <IconButton icon="refresh" label="Refresh now" onClick={refreshData} />
              <AskLauncher />
              <Bell />
              {ai?.enabled && !narrow && (
                <a href={ai.can_admin ? "#/ai" : undefined} title={ai.provider
                   ? `AI on · ${ai.provider.label} (${ai.provider.model})` : "AI on"}
                   style={{ textDecoration: "none" }}>
                  <Pill tone="info">AI on</Pill>
                </a>
              )}
            </div>

            {!narrow && (
              <span aria-hidden style={{ width: 1, height: 24, background: "var(--border)" }} />
            )}

            <MiniButton icon="filter" active={filtersOpen}
                        onClick={() => setFiltersOpen((v) => !v)}
                        title="Show or hide global filters">
              {narrow ? <span style={{ position: "absolute", width: 1, height: 1,
                                       overflow: "hidden", clip: "rect(0 0 0 0)" }}>Filters</span>
                : "Filters"}
              {activeFilterCount > 0 && (
                <span className="tnum" style={{
                  minWidth: 18, height: 18, padding: "0 5px", borderRadius: 999,
                  background: "var(--accent-solid)", color: "#fff",
                  fontSize: "var(--fs-xs)", fontWeight: 600, lineHeight: "18px",
                  textAlign: "center",
                }}>{activeFilterCount}</span>
              )}
            </MiniButton>
          </div>

          {filtersOpen && <FilterBar />}
        </div>

        <div style={{ padding: narrow ? "20px 16px 28px" : "24px 24px 32px",
                      flex: 1, minWidth: 0 }}>
          {!page.bare && <header style={{ marginBottom: 18 }}>
            <h1 style={{ margin: 0, fontSize: "var(--fs-lg)", fontWeight: 650,
                         letterSpacing: "-.01em", color: "var(--text-primary)" }}>
              {page.label}
            </h1>
            <p style={{ margin: "2px 0 0", fontSize: "var(--fs-base)",
                        color: "var(--text-secondary)" }}>{page.desc}</p>
          </header>}
          <Current />
        </div>

        <footer style={{
          display: "flex", alignItems: "center", justifyContent: "center",
          gap: 8, padding: "16px 24px 24px", fontSize: "var(--fs-sm)",
          color: "var(--text-secondary)", flexWrap: "wrap",
        }}>
          <span>© {new Date().getFullYear()} Data Edge Ltd</span>
          <span aria-hidden style={{ opacity: .5 }}>·</span>
          <span>Powered by Data Edge</span>
          <span aria-hidden style={{ opacity: .5 }}>·</span>
          <span>FTP Profitability — Funds Transfer Pricing analytics</span>
        </footer>
      </main>
    </div>
  );
}

/** The navigation column: the icon rail at rest, or the full menu laid over
 *  the page when opened. The rail keeps every destination one click away
 *  while giving the width to the charts. */
function Sidebar({ items, view, rail, drawer = false, onClose }: {
  items: typeof NAV; view: string; rail: boolean; drawer?: boolean; onClose?: () => void;
}) {
  const { me, logout, theme, toggleTheme } = useApp();
  const [menuOpen, setMenuOpen] = useState(false);
  const asideRef = useRef<HTMLElement>(null);

  // Opened as an overlay, the menu takes focus on the current page's link, so
  // a keyboard user lands inside it rather than behind it.
  useEffect(() => {
    if (!drawer) return;
    asideRef.current?.querySelector<HTMLElement>('[aria-current="page"], .nav-item')?.focus();
  }, [drawer]);
  const initials = (me?.full_name ?? "?").split(/\s+/).filter(Boolean)
    .slice(0, 2).map((w) => w[0]!.toUpperCase()).join("");

  const avatar = (
    <span aria-hidden style={{
      width: 32, height: 32, borderRadius: 999, flexShrink: 0,
      display: "grid", placeItems: "center",
      background: "var(--accent-soft)", color: "var(--accent)",
      fontSize: "var(--fs-sm)", fontWeight: 700,
    }}>{initials}</span>
  );
  const themeIcon = theme === "dark" ? "sun" : "moon";
  const themeLabel = theme === "dark" ? "Switch to light theme" : "Switch to dark theme";

  return (
    <aside ref={asideRef}
           className={`glass ${rail ? "rail" : drawer ? "nav-overlay" : ""}`}
           aria-label={drawer ? "Navigation menu" : undefined} style={{
      width: rail ? 64 : 240, flexShrink: 0,
      borderRight: "1px solid var(--glass-edge)", display: "flex", flexDirection: "column",
      height: "100vh", paddingTop: "env(safe-area-inset-top, 0px)",
      ...(drawer
        ? { position: "fixed", left: 0, top: 0, zIndex: 70,
            boxShadow: "0 12px 32px rgba(16,24,40,.18), 0 2px 6px rgba(16,24,40,.08)" }
        : { position: "sticky", top: 0 }),
    }}>
      <div style={{
        height: 56, display: "flex", alignItems: "center", flexShrink: 0,
        padding: rail ? 0 : "0 16px", justifyContent: rail ? "center" : "space-between",
        borderBottom: "1px solid var(--border)",
      }}>
        {rail ? (
          <img src="/dataedge_mark.png" alt="Data Edge" style={{ width: 30, height: 30 }} />
        ) : (
          <>
            <div style={{ minWidth: 0 }}>
              <BrandLogo height={30} />
            </div>
            {drawer && <IconButton icon="close" label="Close navigation" onClick={onClose} />}
          </>
        )}
      </div>
      {!rail && (
        <div style={{ padding: "12px 16px 0", fontSize: "var(--fs-xs)", fontWeight: 600,
                      letterSpacing: ".04em", color: "var(--text-secondary)" }}>
          FTP PROFITABILITY
        </div>
      )}

      <nav aria-label="Main" style={{ flex: 1, padding: rail ? "12px 10px" : "8px 12px",
                                       overflowY: "auto" }}>
        {["Analyse", "Intelligence", "Operate"].map((group) => {
          const groupItems = items.filter((n) => n.group === group);
          if (!groupItems.length) return null;
          return (
            <div key={group} style={{ marginTop: 12 }}>
              {rail ? (
                <div aria-hidden style={{ height: 1, background: "var(--border)",
                                          margin: "0 8px 10px" }} />
              ) : (
                <div style={{
                  fontSize: "var(--fs-xs)", fontWeight: 600, letterSpacing: ".06em",
                  textTransform: "uppercase", color: "var(--text-muted)",
                  padding: "0 10px 6px",
                }}>{group}</div>
              )}
              {groupItems.map((n) => (
                <a key={n.id} href={`#/${n.id}`} className="nav-item"
                   // Choosing a page puts the menu away -- including the page
                   // already open, which fires no hash change of its own.
                   onClick={onClose}
                   aria-current={view === n.id ? "page" : undefined}
                   title={rail ? n.label : undefined}
                   aria-label={rail ? n.label : undefined}>
                  <Icon name={n.icon} />
                  {!rail && n.label}
                </a>
              ))}
            </div>
          );
        })}
      </nav>

      <div style={{ padding: rail ? "12px 0" : 12, borderTop: "1px solid var(--border)",
                    position: "relative" }}>
        {rail ? (
          <div style={{ display: "grid", placeItems: "center" }}>
            <button type="button" onClick={() => setMenuOpen((v) => !v)}
                    aria-label={`${me?.full_name ?? "Account"} — account menu`}
                    aria-expanded={menuOpen}
                    style={{ padding: 0, border: "none", background: "none",
                             cursor: "pointer", borderRadius: 999 }}>
              {avatar}
            </button>
            {menuOpen && (
              <div role="menu" style={{
                position: "absolute", left: 56, bottom: 10, zIndex: 80, width: 220,
                background: "var(--surface-1)", border: "1px solid var(--border)",
                borderRadius: "var(--radius-sm)", boxShadow: "var(--shadow-md)", padding: 8,
              }}>
                <div style={{ padding: "4px 8px 8px" }}>
                  <div style={{ fontSize: "var(--fs-base)", fontWeight: 600 }}>{me?.full_name}</div>
                  <div style={{ fontSize: "var(--fs-sm)", color: "var(--text-muted)" }}>
                    {me?.scope_label}
                  </div>
                </div>
                <Button variant="ghost" size="sm" icon={themeIcon} style={{ width: "100%",
                        justifyContent: "flex-start" }}
                        onClick={() => { toggleTheme(); setMenuOpen(false); }}>
                  {theme === "dark" ? "Light theme" : "Dark theme"}
                </Button>
                <Button variant="ghost" size="sm" icon="logout" style={{ width: "100%",
                        justifyContent: "flex-start" }} onClick={logout}>
                  Sign out
                </Button>
              </div>
            )}
          </div>
        ) : (
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            {avatar}
            <div style={{ minWidth: 0, flex: 1 }}>
              <div style={{ fontSize: "var(--fs-base)", fontWeight: 600,
                            color: "var(--text-primary)", overflow: "hidden",
                            textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {me?.full_name}
              </div>
              <div style={{ fontSize: "var(--fs-sm)", color: "var(--text-muted)",
                            overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {me?.scope_label}
              </div>
            </div>
            <IconButton icon={themeIcon} label={themeLabel} onClick={toggleTheme} />
            <IconButton icon="logout" label="Sign out" onClick={logout} />
          </div>
        )}
      </div>
    </aside>
  );
}

/** The wordmark in whichever cut suits the theme; CSS picks, so no flash.
 *  The images are trimmed to the artwork, so `height` is the letterform's. */
function BrandLogo({ height, center = false }: { height: number; center?: boolean }) {
  const style: React.CSSProperties = { height, width: "auto", maxWidth: "100%",
                                       ...(center ? { marginInline: "auto" } : {}) };
  return (
    <>
      <img src="/dataedge_wordmark.png" alt="Data Edge Ltd" className="logo-light" style={style} />
      <img src="/dataedge_wordmark_dark.png" alt="Data Edge Ltd" className="logo-dark" style={style} />
    </>
  );
}

function Centered({ children }: { children: React.ReactNode }) {
  return <div style={{ display: "grid", placeItems: "center", minHeight: "100vh",
                       background: "var(--page)" }}>{children}</div>;
}

function ForcePasswordChange() {
  const { me, refreshMe, logout } = useApp();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const field: React.CSSProperties = {
    width: "100%", minHeight: 38, fontSize: "var(--fs-md)",
  };
  const tooShort = next.length > 0 && next.length < 12;
  const mismatch = confirm.length > 0 && confirm !== next;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      await api.changePassword(current, next);
      await refreshMe();
    } catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  }

  return (
    <Centered>
      <div style={{ width: "min(400px, 92vw)" }}>
        <div style={{ textAlign: "center", marginBottom: 14 }}>
          <BrandLogo height={36} center />
        </div>
        <Card title="Choose a new password"
              subtitle={`${me!.full_name} — the account is still on its initial password`}>
          <form onSubmit={submit} style={{ display: "flex", flexDirection: "column",
                                           gap: 12, padding: "6px 4px 2px" }}>
            <p style={{ margin: 0, fontSize: "var(--fs-base)", color: "var(--text-secondary)" }}>
              The seeded password is known to anyone who can read the setup
              notes, so it has to be replaced before the account can be used.
            </p>
            {([["Current password", current, setCurrent, "current-password"],
               ["New password", next, setNext, "new-password"],
               ["Confirm new password", confirm, setConfirm, "new-password"]] as const)
              .map(([label, value, set, ac]) => (
              <label key={label} style={{ fontSize: "var(--fs-sm)" }}>
                <span style={{ display: "block", marginBottom: 4,
                               color: "var(--text-muted)" }}>{label}</span>
                <input style={field} type="password" value={value} autoComplete={ac}
                       onChange={(e) => set(e.target.value)} />
              </label>
            ))}
            {tooShort && (
              <span style={{ fontSize: "var(--fs-sm)", color: "var(--text-muted)" }}>
                At least 12 characters.
              </span>
            )}
            {mismatch && (
              <span style={{ fontSize: "var(--fs-sm)", color: "var(--status-critical)" }}>
                The two new passwords do not match.
              </span>
            )}
            {error && (
              <div style={{ fontSize: "var(--fs-base)" }}>
                <Pill tone="critical">Could not change</Pill>
                <span style={{ marginLeft: 6, color: "var(--text-secondary)" }}>{error}</span>
              </div>
            )}
            <Button type="submit" variant="primary"
                    disabled={busy || !current || next.length < 12 || next !== confirm}>
              {busy ? "Saving…" : "Set password and continue"}
            </Button>
            <button type="button" onClick={logout} style={{
              background: "none", border: "none", color: "var(--text-muted)",
              fontSize: "var(--fs-sm)", cursor: "pointer", padding: 0, textAlign: "center",
            }}>Sign out instead</button>
          </form>
        </Card>
      </div>
    </Centered>
  );
}

function Login() {
  const { login } = useApp();
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    try { await login(username, password); }
    catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  }

  const field: React.CSSProperties = {
    width: "100%", minHeight: 40, padding: "0 12px", fontSize: "var(--fs-md)",
  };
  const group: React.CSSProperties = {
    fontSize: "var(--fs-sm)", display: "block", marginBottom: 6,
    color: "var(--text-secondary)", fontWeight: 600,
  };

  return (
    <div className="canvas-art" style={{ minHeight: "100vh", display: "flex",
                                         flexDirection: "column" }}>
      <div style={{ flex: 1, display: "grid", placeItems: "center", padding: "36px 20px 60px" }}>
        <div style={{ width: "min(400px, 100%)", display: "flex", flexDirection: "column" }}>
          {/* Brand */}
          <div style={{ textAlign: "center", marginBottom: 26 }}>
            <BrandLogo height={44} center />
          </div>

          {/* Card */}
          <div style={{
            background: "var(--surface-1)", borderRadius: "var(--radius)",
            border: "1px solid var(--border)", boxShadow: "var(--shadow-md)",
            padding: "32px 28px 28px",
          }}>
            <h1 style={{ margin: 0, fontSize: "var(--fs-lg)", fontWeight: 650,
                         color: "var(--text-primary)" }}>Sign in</h1>
            <p style={{ margin: "4px 0 22px", fontSize: "var(--fs-base)", color: "var(--text-muted)",
                        lineHeight: 1.5 }}>
              FTP Profitability — Funds Transfer Pricing analytics.
            </p>

            <form onSubmit={submit} style={{ display: "flex", flexDirection: "column", gap: 14 }}>
              <label style={{ display: "block" }}>
                <span style={group}>Username</span>
                <input style={field} value={username} autoComplete="username"
                       onChange={(e) => setUsername(e.target.value)} />
              </label>
              <label style={{ display: "block" }}>
                <span style={group}>Password</span>
                <input style={field} type="password" value={password}
                       autoComplete="current-password"
                       onChange={(e) => setPassword(e.target.value)} />
              </label>

              {error && (
                <div style={{
                  display: "flex", gap: 8, alignItems: "flex-start", fontSize: "var(--fs-base)",
                  color: "var(--status-critical)",
                  background: "color-mix(in srgb, var(--status-critical) var(--tint), transparent)",
                  border: "1px solid color-mix(in srgb, var(--status-critical) 25%, transparent)",
                  borderRadius: "var(--radius-sm)", padding: "8px 10px",
                }}>
                  <span aria-hidden style={{ fontWeight: 700 }}>⚠</span>
                  <span>{error}</span>
                </div>
              )}

              <Button type="submit" variant="primary" disabled={busy || !password}
                      style={{ width: "100%", height: 40, marginTop: 4 }}>
                {busy ? "Signing in…" : "Sign in"}
              </Button>
            </form>
          </div>

          {/* Footer */}
          <p style={{ textAlign: "center", margin: "20px 0 0", fontSize: "var(--fs-sm)",
                      color: "var(--text-muted)" }}>
            © {new Date().getFullYear()} Data Edge Ltd · Powered by Data Edge
          </p>
        </div>
      </div>
    </div>
  );
}

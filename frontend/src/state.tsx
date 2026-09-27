import {
  createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useState,
} from "react";
import { api, Branch, DataVersion, Filters, Me, Node_, Product, setToken } from "./api";
import { aiApi, AiStatus } from "./ai/api";

interface Ctx {
  me: Me | null;
  ready: boolean;
  login: (u: string, p: string) => Promise<void>;
  logout: () => void;
  can: (perm: string) => boolean;
  refreshMe: () => Promise<void>;

  filters: Filters;
  setFilters: (f: Filters | ((p: Filters) => Filters)) => void;
  resetFilters: () => void;

  branches: Branch[];
  products: Product[];
  divisions: Node_[];
  districts: Node_[];
  refreshMasters: () => Promise<void>;

  theme: "light" | "dark";
  toggleTheme: () => void;

  /** Bumps whenever the server's data fingerprint moves. Every data hook
   *  depends on it, so an upload made anywhere refreshes every open view. */
  dataVersion: number;
  dataInfo: DataVersion | null;
  refreshData: () => void;
  lastSync: Date | null;

  /** The optional AI module: null when the backend runs without it. */
  ai: AiStatus | null;
  refreshAi: () => Promise<void>;
}

const C = createContext<Ctx>(null!);
export const useApp = () => useContext(C);

/** Filters live in the URL hash, so any view is a shareable link and the back
 *  button works across drilldowns. */
function readFilters(): Filters {
  const raw = new URLSearchParams(location.hash.replace(/^#\/?[^?]*\??/, ""));
  const f: Filters = {};
  const s = (k: keyof Filters) => raw.get(k as string) ?? undefined;
  f.date_from = s("date_from");
  f.date_to = s("date_to");
  f.branch_category = s("branch_category");
  f.account_no = s("account_no");
  f.side = s("side") as Filters["side"];
  f.ftp_sign = s("ftp_sign") as Filters["ftp_sign"];
  const div = raw.get("division_id"); if (div) f.division_id = +div;
  const dis = raw.get("district_id"); if (dis) f.district_id = +dis;
  const b = raw.getAll("branch_id").map(Number).filter(Boolean);
  if (b.length) f.branch_id = b;
  const p = raw.getAll("product_code").filter(Boolean);
  if (p.length) f.product_code = p;
  return Object.fromEntries(Object.entries(f).filter(([, v]) => v !== undefined)) as Filters;
}

function writeFilters(view: string, f: Filters) {
  const p = new URLSearchParams();
  Object.entries(f).forEach(([k, v]) => {
    if (v === undefined || v === null || v === "") return;
    if (Array.isArray(v)) v.forEach((x) => p.append(k, String(x)));
    else p.append(k, String(v));
  });
  const q = p.toString();
  const next = `#/${view}${q ? `?${q}` : ""}`;
  if (location.hash !== next) history.replaceState(null, "", next);
}

/** The view the hash names, defaulting to the landing page.
 *
 *  Must stay in step with the first entry of NAV and with App's component
 *  lookup: they disagreed before, so an empty hash rendered Overview while
 *  Daily was the intended landing page. Sign-in lands here too (see login). */
export const DEFAULT_VIEW = "basic";

export function currentView(): string {
  return location.hash.replace(/^#\/?/, "").split("?")[0] || DEFAULT_VIEW;
}

export function AppProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [ready, setReady] = useState(false);
  const [filters, setFiltersRaw] = useState<Filters>(readFilters);
  const [branches, setBranches] = useState<Branch[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [divisions, setDivisions] = useState<Node_[]>([]);
  const [districts, setDistricts] = useState<Node_[]>([]);
  const [dataVersion, setDataVersion] = useState(0);
  const [dataInfo, setDataInfo] = useState<DataVersion | null>(null);
  const [lastSync, setLastSync] = useState<Date | null>(null);
  const [ai, setAi] = useState<AiStatus | null>(null);
  const [theme, setTheme] = useState<"light" | "dark">(
    () => (localStorage.getItem("ftp_theme") as "light" | "dark") ??
          (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"),
  );

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("ftp_theme", theme);
  }, [theme]);

  // Re-read the filters whenever the hash changes, not only at mount.
  // Without this, back/forward and a link pasted into an already-open tab
  // change the view but silently keep the old filters -- the page then shows
  // one thing and claims another.
  useEffect(() => {
    const onHash = () => setFiltersRaw(readFilters());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const refreshMasters = useCallback(async () => {
    const [b, p, dv, ds] = await Promise.all([
      api.branches(true).catch(() => []),
      api.products().catch(() => []),
      api.divisions().catch(() => []),
      api.districts().catch(() => []),
    ]);
    setBranches(b); setProducts(p); setDivisions(dv); setDistricts(ds);
  }, []);

  const refreshAi = useCallback(async () => {
    try { setAi(await aiApi.status()); } catch { /* the next poll retries */ }
  }, []);

  useEffect(() => {
    (async () => {
      try {
        const m = await api.me();
        setMe(m);
        await refreshMasters();
      } catch { setToken(null); }
      finally { setReady(true); }
    })();
  }, [refreshMasters]);

  /** Poll the fingerprint rather than the data: it is a few scalar lookups, so
   *  it stays cheap at any volume, and only a real change costs a refetch.
   *  Polling pauses while the tab is hidden and catches up on focus. */
  useEffect(() => {
    if (!me) return;
    let stop = false;
    let seen: string | null = null;

    const check = async () => {
      if (document.hidden || stop) return;
      try {
        const v = await api.dataVersion();
        setDataInfo(v);
        setLastSync(new Date());
        if (seen !== null && v.version !== seen) setDataVersion((n) => n + 1);
        seen = v.version;
      } catch { /* a failed poll is not worth surfacing; the next one retries */ }
      // The AI switch rides the same poll, so turning it off reaches every
      // open session within one tick.
      refreshAi();
    };

    check();
    const id = setInterval(check, 15_000);
    const onFocus = () => check();
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onFocus);
    return () => {
      stop = true;
      clearInterval(id);
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onFocus);
    };
  }, [me, refreshAi]);

  const setFilters = useCallback((f: Filters | ((p: Filters) => Filters)) => {
    setFiltersRaw((prev) => {
      const next = typeof f === "function" ? f(prev) : f;
      writeFilters(currentView(), next);
      return next;
    });
  }, []);

  const value = useMemo<Ctx>(() => ({
    me, ready,
    login: async (u, p) => {
      const r = await api.login(u, p);
      setToken(r.access_token);
      const m = await api.me();
      setMe(m);
      await refreshMasters();
      location.hash = `#/${DEFAULT_VIEW}`;
    },
    logout: () => { setToken(null); setMe(null); setAi(null); location.hash = `#/${DEFAULT_VIEW}`; },
    can: (perm) => Boolean(me?.permissions.includes(perm)),
    refreshMe: async () => setMe(await api.me()),
    filters, setFilters,
    resetFilters: () => setFilters({}),
    branches, products, divisions, districts, refreshMasters,
    theme, toggleTheme: () => setTheme((t) => (t === "dark" ? "light" : "dark")),
    dataVersion, dataInfo, lastSync,
    // Called straight after an upload so the page updates without waiting for
    // the next poll tick.
    refreshData: () => setDataVersion((n) => n + 1),
    ai, refreshAi,
  }), [me, ready, filters, setFilters, branches, products, divisions, districts,
       theme, refreshMasters, dataVersion, dataInfo, lastSync, ai, refreshAi]);

  return <C.Provider value={value}>{children}</C.Provider>;
}

/** Tiny async hook: keeps the previous value visible while refetching, so the
 *  page dims rather than flashing a skeleton.
 *
 *  `dataVersion` is folded into the dependency list here rather than at every
 *  call site, so no view can forget to be reactive to new data. */
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]) {
  const { dataVersion } = useApp();
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    fn()
      .then((d) => { if (alive) { setData(d); setError(null); } })
      .catch((e) => { if (alive) setError(e?.message ?? String(e)); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, dataVersion]);

  return { data, loading, error };
}

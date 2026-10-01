import { useEffect, useMemo, useRef, useState } from "react";
import Chart, { axisCommon, baseOption, useTokens } from "../components/Chart";
import { Button, Card, Empty, Grid, MiniButton, Pill, Table } from "../components/ui";
import { compact } from "../format";
import { useApp } from "../state";
import { clearPageExtra, setPageExtra } from "./pageContext";
import {
  ScenarioGroup, ScenarioParams, ScenarioPreset, ScenarioResult, scenarioApi,
} from "./api";

const muted: React.CSSProperties = { color: "var(--text-muted)", fontSize: "var(--fs-xs)" };
const field: React.CSSProperties = {
  width: "100%", padding: "8px 10px", border: "1px solid var(--border)", borderRadius: 8,
  background: "var(--surface-1)", color: "var(--text-primary)", font: "inherit",
};

const tk = (v: number | null | undefined, signed = false) => {
  if (v == null) return "—";
  const x = Number(v);
  return `${signed && x > 0 ? "+" : ""}${x < 0 ? "-" : ""}৳${compact(Math.abs(x))}`;
};

/** `#/scenario?preset=match_pcb` or `?s=<json>` opens the lab on a scenario. */
function fromHash(): { preset?: string; s?: Partial<ScenarioParams> } {
  const q = new URLSearchParams(location.hash.split("?")[1] ?? "");
  const out: { preset?: string; s?: Partial<ScenarioParams> } = {};
  if (q.get("preset")) out.preset = q.get("preset")!;
  try { if (q.get("s")) out.s = JSON.parse(q.get("s")!); } catch { /* ignore a bad link */ }
  return out;
}

export function scenarioLink(s: Partial<ScenarioParams> | string) {
  return typeof s === "string" ? `#/scenario?preset=${encodeURIComponent(s)}`
    : `#/scenario?s=${encodeURIComponent(JSON.stringify(s))}`;
}

function Slider({ label, value, onChange, min, max, step, fmt, hint }: {
  label: string; value: number; onChange: (v: number) => void; min: number; max: number;
  step: number; fmt: (v: number) => string; hint?: string;
}) {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <span style={{ display: "flex", justifyContent: "space-between", fontSize: "var(--fs-sm)" }}>
        <span style={{ fontWeight: 600 }}>{label}</span>
        <span className="tnum" style={{ fontWeight: 650, color: "var(--accent)" }}>{fmt(value)}</span>
      </span>
      <input type="range" min={min} max={max} step={step} value={value}
             onChange={(e) => onChange(Number(e.target.value))} style={{ width: "100%" }} />
      {hint && <span style={muted}>{hint}</span>}
    </label>
  );
}

const bp = (v: number) => `${v > 0 ? "+" : ""}${v} bp`;
const share = (v: number) => `${Math.round(v * 100)}%`;
const pctFmt = (v: number) => `${v > 0 ? "+" : ""}${v}%`;

export default function Scenario() {
  const { ai } = useApp();
  const [presets, setPresets] = useState<ScenarioPreset[]>([]);
  const [defaults, setDefaults] = useState<ScenarioParams | null>(null);
  const [s, setS] = useState<ScenarioParams | null>(null);
  const [res, setRes] = useState<ScenarioResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [active, setActive] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [reading, setReading] = useState(false);
  const [assumptions, setAssumptions] = useState<string[]>([]);
  const [saved, setSaved] = useState<{ id: number; name: string; scenario: ScenarioParams;
                                       summary: Record<string, number> }[]>([]);
  const run = useRef(0);

  useEffect(() => {
    scenarioApi.presets().then((p) => {
      setPresets(p.items); setDefaults(p.defaults);
      const h = fromHash();
      const pre = h.preset ? p.items.find((x) => x.key === h.preset) : null;
      if (pre) { setS(pre.scenario); setActive(pre.key); }
      else setS({ ...p.defaults, ...(h.s ?? {}) });
    }).catch((e) => setErr((e as Error).message));
    scenarioApi.saved().then((x) => setSaved(x.items)).catch(() => undefined);
  }, []);

  // Recompute as the sliders move, a moment after the last change.
  useEffect(() => {
    if (!s) return;
    const id = ++run.current;
    setBusy(true);
    const t = window.setTimeout(() => {
      scenarioApi.run(s).then((r) => { if (id === run.current) { setRes(r); setErr(null); } })
        .catch((e) => { if (id === run.current) setErr((e as Error).message); })
        .finally(() => { if (id === run.current) setBusy(false); });
    }, 250);
    return () => window.clearTimeout(t);
  }, [s]);

  // Ask FTP reads the scenario on screen: "and if loans pass 90%?" builds on it.
  useEffect(() => {
    if (!s) return;
    const bits = [s.market_bp ? `market ${s.market_bp > 0 ? "+" : ""}${s.market_bp} bp` : "",
                  s.competitor_bp ? `other banks ${s.competitor_bp > 0 ? "+" : ""}${s.competitor_bp} bp` : "",
                  Object.keys(s.product_rate_bp).length ? `${Object.keys(s.product_rate_bp).length} product rate change(s)` : "",
                  s.deposit_growth_pct ? `deposits ${s.deposit_growth_pct > 0 ? "+" : ""}${s.deposit_growth_pct}%` : "",
                  `${s.horizon_months} mo`].filter(Boolean);
    setPageExtra({ scenario: s as unknown as Record<string, unknown>,
                   scenarioLabel: `scenario: ${bits.join(", ")}` });
  }, [s]);
  useEffect(() => () => clearPageExtra(), []);

  const set = (patch: Partial<ScenarioParams>) => { setS((x) => (x ? { ...x, ...patch } : x)); setActive(null); };

  const read = async () => {
    setReading(true); setErr(null);
    try {
      const r = await scenarioApi.parse(text);
      setS({ ...(defaults as ScenarioParams), ...r.scenario });
      setAssumptions(r.assumptions); setActive(null);
    } catch (e) { setErr((e as Error).message); } finally { setReading(false); }
  };

  const save = async () => {
    if (!s || !res?.available) return;
    const name = window.prompt("Name this scenario", presets.find((p) => p.key === active)?.label ?? "My scenario");
    if (!name) return;
    await scenarioApi.save(name, s, { bank_nii: res.change.bank_nii, branch_ftp: res.change.branch_ftp,
                                      deposits: res.change.deposits });
    setSaved((await scenarioApi.saved()).items);
  };

  if (err && !s) return <Card><Empty title="The scenario lab could not start" hint={err} /></Card>;
  if (!s) return <p style={muted}>Loading…</p>;

  return (
    <Grid cols="minmax(280px, 340px) minmax(0, 1fr)" gap={16}>
      <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        {ai?.can_chat && (
          <Card title="Describe a what-if">
            <textarea style={{ ...field, minHeight: 70, resize: "vertical" }} value={text}
                      placeholder="e.g. Bangladesh Bank hikes 50 bp and we pass only 30% to term deposits"
                      onChange={(e) => setText(e.target.value)} />
            <div style={{ display: "flex", gap: 8, marginTop: 8, alignItems: "center" }}>
              <Button variant="primary" disabled={reading || text.trim().length < 5} onClick={read}>
                {reading ? "Reading…" : "Set it up"}</Button>
              <span style={muted}>The AI reads your sentence into the settings below; the figures are computed, not written.</span>
            </div>
            {assumptions.length > 0 && (
              <ul style={{ ...muted, margin: "10px 0 0", paddingLeft: 18 }}>
                {assumptions.map((a) => <li key={a}>{a}</li>)}
              </ul>
            )}
          </Card>
        )}
        <Card title="Start from">
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {presets.map((p) => (
              <span key={p.key} title={p.why}>
                <MiniButton active={active === p.key}
                            style={{ whiteSpace: "normal", height: "auto", minHeight: 28, padding: "5px 10px",
                                     textAlign: "left" }}
                            onClick={() => { setS(p.scenario); setActive(p.key); setAssumptions([]); }}>
                  {p.label}</MiniButton>
              </span>
            ))}
            {defaults && <MiniButton onClick={() => { setS(defaults); setActive(null); }}>Reset</MiniButton>}
          </div>
          {active && <p style={{ ...muted, margin: "8px 0 0" }}>{presets.find((p) => p.key === active)?.why}</p>}
        </Card>
        <Card title="Settings">
          <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            <Slider label="Market rates move" value={s.market_bp} min={-300} max={300} step={25} fmt={bp}
                    onChange={(v) => set({ market_bp: v })} hint="The policy rate and the curve, in parallel." />
            <Slider label="FTP benchmarks follow" value={s.bench_follow} min={0} max={1} step={0.05} fmt={share}
                    onChange={(v) => set({ bench_follow: v })} hint="Term products; CASA benchmarks follow policy." />
            <Slider label="Passed to term depositors" value={s.deposit_pass} min={0} max={1} step={0.05} fmt={share}
                    onChange={(v) => set({ deposit_pass: v })} />
            <Slider label="Passed to savings & current" value={s.demand_pass} min={0} max={1} step={0.05} fmt={share}
                    onChange={(v) => set({ demand_pass: v })} />
            <Slider label="Passed to borrowers" value={s.loan_pass} min={0} max={1} step={0.05} fmt={share}
                    onChange={(v) => set({ loan_pass: v })} />
            <Slider label="Other banks' deposit rates" value={s.competitor_bp} min={-100} max={200} step={25} fmt={bp}
                    onChange={(v) => set({ competitor_bp: v })} hint="Beyond the market move." />
            <Slider label="Depositors' sensitivity" value={s.elasticity} min={0} max={10} step={0.5}
                    fmt={(v) => `${v}% per 100 bp`} onChange={(v) => set({ elasticity: v })}
                    hint="Term deposits lost per 100 bp we fall behind other banks." />
            <Slider label="Deposit growth" value={s.deposit_growth_pct} min={-20} max={20} step={1} fmt={pctFmt}
                    onChange={(v) => set({ deposit_growth_pct: v })} />
            <Slider label="Loan growth" value={s.loan_growth_pct} min={-20} max={20} step={1} fmt={pctFmt}
                    onChange={(v) => set({ loan_growth_pct: v })} />
            <label style={{ display: "flex", justifyContent: "space-between", alignItems: "center",
                            fontSize: "var(--fs-sm)", fontWeight: 600 }}>Horizon
              <select style={{ ...field, width: 140 }} value={s.horizon_months}
                      onChange={(e) => set({ horizon_months: Number(e.target.value) })}>
                {[1, 3, 6, 12].map((m) => <option key={m} value={m}>{m} month{m > 1 ? "s" : ""}</option>)}
              </select>
            </label>
            {Object.keys(s.product_rate_bp).length > 0 && (
              <div>
                <div style={{ fontWeight: 600, fontSize: "var(--fs-sm)", marginBottom: 4 }}>Customer rate changes</div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                  {Object.entries(s.product_rate_bp).map(([k, v]) => (
                    <MiniButton key={k} title="Remove" onClick={() => {
                      const rest = { ...s.product_rate_bp }; delete rest[k]; set({ product_rate_bp: rest });
                    }}>{k} {bp(v)} ✕</MiniButton>))}
                </div>
              </div>
            )}
          </div>
        </Card>
        <Card title="Saved" actions={<MiniButton disabled={!res?.available} onClick={save}>Save this</MiniButton>}>
          {saved.length === 0 ? <p style={muted}>Nothing saved yet. You can keep up to 30.</p> : (
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {saved.map((x) => (
                <div key={x.id} style={{ display: "flex", gap: 6, alignItems: "center" }}>
                  <MiniButton onClick={() => { setS(x.scenario); setActive(null); }}>{x.name}</MiniButton>
                  <span style={muted} className="tnum">NII {tk(x.summary.bank_nii, true)}/mo</span>
                  <span style={{ flex: 1 }} />
                  <MiniButton title="Delete" onClick={async () => {
                    await scenarioApi.remove(x.id); setSaved((l) => l.filter((y) => y.id !== x.id));
                  }}>✕</MiniButton>
                </div>))}
            </div>)}
        </Card>
      </div>
      <Results res={res} busy={busy} err={err} />
    </Grid>
  );
}

function Tile({ label, value, sub, good }: { label: string; value: string; sub?: string; good?: boolean | null }) {
  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: "var(--radius)", padding: "12px 14px",
                  background: "var(--surface-1)", minWidth: 0 }}>
      <div style={{ ...muted, fontWeight: 600, textTransform: "uppercase", letterSpacing: ".05em" }}>{label}</div>
      <div className="tnum" style={{ fontSize: 24, fontWeight: 700, color: good == null ? "var(--text-primary)"
        : good ? "var(--delta-up)" : "var(--delta-down)" }}>{value}</div>
      {sub && <div style={muted}>{sub}</div>}
    </div>
  );
}

function Results({ res, busy, err }: { res: ScenarioResult | null; busy: boolean; err: string | null }) {
  const t = useTokens();
  const wf = useMemo(() => {
    if (!res?.available) return null;
    const items = res.waterfall;
    const total = Number(res.change.bank_nii);
    // A waterfall: invisible base, then each step up or down, then the total.
    let run = 0;
    const base: number[] = [], up: number[] = [], down: number[] = [];
    for (const it of items) {
      const v = Number(it.value);
      const lo = v >= 0 ? run : run + v;
      base.push(lo); up.push(v >= 0 ? v : 0); down.push(v < 0 ? -v : 0);
      run += v;
    }
    base.push(Math.min(0, total)); up.push(total >= 0 ? total : 0); down.push(total < 0 ? -total : 0);
    const cats = [...items.map((i) => i.label), "Change in bank NII"];
    return {
      ...baseOption(t), legend: { show: false },
      grid: { left: 8, right: 16, top: 16, bottom: 8, containLabel: true },
      tooltip: { ...baseOption(t).tooltip, trigger: "axis" as const,
                 formatter: (raw: unknown) => {
                   const i = (raw as { dataIndex: number }[])[0].dataIndex;
                   const v = i < items.length ? Number(items[i].value) : total;
                   return `<b>${cats[i]}</b><br/>${tk(v, true)} a month`;
                 } },
      xAxis: { type: "category" as const, data: cats, ...axisCommon(t), splitLine: { show: false },
               axisLabel: { color: t.muted, fontSize: 10, interval: 0, width: 90, overflow: "break" as const } },
      yAxis: { type: "value" as const, ...axisCommon(t), axisLine: { show: false },
               axisLabel: { color: t.muted, fontSize: 10, formatter: (v: number) => compact(v) } },
      series: [
        { type: "bar" as const, stack: "w", data: base, itemStyle: { color: "transparent" }, silent: true },
        { type: "bar" as const, stack: "w", data: up, itemStyle: { color: t.deltaUp, borderRadius: 3 } },
        { type: "bar" as const, stack: "w", data: down, itemStyle: { color: t.deltaDown, borderRadius: 3 } },
      ],
    } as never;
  }, [res, t]);

  const pathOpt = useMemo(() => {
    if (!res?.available) return null;
    return {
      ...baseOption(t), grid: { left: 8, right: 16, top: 28, bottom: 8, containLabel: true },
      legend: { ...baseOption(t).legend, top: 0, left: 0 },
      tooltip: { ...baseOption(t).tooltip, trigger: "axis" as const },
      xAxis: { type: "category" as const, data: res.path.map((p) => `day ${p.day}`), ...axisCommon(t),
               splitLine: { show: false } },
      yAxis: { type: "value" as const, ...axisCommon(t), axisLine: { show: false },
               axisLabel: { color: t.muted, fontSize: 10, formatter: (v: number) => compact(v) } },
      series: [
        { name: "Bank NII, change per day", type: "line" as const, symbol: "none",
          data: res.path.map((p) => Number(p.bank_nii_per_day)), lineStyle: { width: 2, color: t.series[0] },
          itemStyle: { color: t.series[0] } },
        { name: "Branches' FTP profit, change per day", type: "line" as const, symbol: "none",
          data: res.path.map((p) => Number(p.branch_ftp_per_day)), lineStyle: { width: 2, color: t.series[1] },
          itemStyle: { color: t.series[1] } },
      ],
    } as never;
  }, [res, t]);

  if (!res) return <Card><p style={muted}>{err ?? "Running…"}</p></Card>;
  if (!res.available) return <Card><Empty title="No book to simulate" hint={res.reason} /></Card>;
  const c = res.change;
  const dep = Number(c.deposits);
  const nimBp = c.nim != null ? Math.round(Number(c.nim) * 100) : null;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14, opacity: busy ? 0.65 : 1,
                  transition: "opacity .15s" }}>
      {err && <Pill tone="critical">{err}</Pill>}
      <Grid cols="repeat(auto-fit, minmax(170px, 1fr))" gap={10}>
        <Tile label="Bank NII, a month" value={tk(c.bank_nii, true)} good={Number(c.bank_nii) === 0 ? null : Number(c.bank_nii) > 0}
              sub={`from ${tk(res.base.bank_nii)} today`} />
        <Tile label="Branches' FTP profit" value={tk(c.branch_ftp, true)} good={Number(c.branch_ftp) === 0 ? null : Number(c.branch_ftp) > 0}
              sub={`from ${tk(res.base.branch_ftp)} a month`} />
        <Tile label="Treasury's result" value={tk(c.treasury, true)} good={Number(c.treasury) === 0 ? null : Number(c.treasury) > 0}
              sub="the funding centre's share" />
        <Tile label="Deposits" value={tk(dep, true)} good={dep === 0 ? null : dep > 0}
              sub={`${((dep / Number(res.base.deposits)) * 100).toFixed(2)}% of ${tk(res.base.deposits)}`} />
        <Tile label="NIM" value={nimBp == null ? "—" : `${nimBp > 0 ? "+" : ""}${nimBp} bp`}
              good={!nimBp ? null : nimBp > 0}
              sub={res.scenario_totals.nim != null ? `to ${Number(res.scenario_totals.nim).toFixed(2)}%` : undefined} />
      </Grid>
      <Card title="Where the change in bank NII comes from"
            subtitle={`A month, ${res.days} days in, once each product has repriced as far as its term allows. Surplus deposits earn the ${res.market_rate_label} (${Number(res.market_rate).toFixed(2)}%).`}
            footnote={`Base: an average day of ${res.base_window.start} to ${res.base_window.latest} (${res.lines} branch-product lines). Moving FTP benchmarks shifts profit between branches and treasury but leaves bank NII unchanged; only customer rates, balances and the market move NII.`}>
        {wf && <Chart option={wf} height={260} ariaLabel="Waterfall of the change in bank NII" />}
      </Card>
      <Card title="As the scenario phases in" subtitle="Change per day: term products reprice as they roll over, so the effect builds to the horizon.">
        {pathOpt && <Chart option={pathOpt} height={200} ariaLabel="Change per day as the scenario phases in" />}
      </Card>
      <Grid cols="repeat(auto-fit, minmax(480px, 1fr))" gap={14}>
        <Card title="By product">
          <Table<ScenarioGroup> rows={[...res.by_product].sort((a, b) => Math.abs(b.nii_change) - Math.abs(a.nii_change))}
                                csvName="scenario-by-product" cols={[
            { key: "label", label: "Product", render: (r) => <div><div style={{ fontWeight: 600 }}>{r.label}</div>
              <div style={muted}>{r.side === "ASSET" ? "Loan" : "Deposit"} · {tk(r.balance_base)}
                {Number(r.balance_new) !== Number(r.balance_base) && <> → {tk(r.balance_new)}</>}</div></div> },
            { key: "nii", label: "NII / month", align: "right", render: (r) => <span className="tnum">{tk(r.nii_change, true)}</span> },
            { key: "ftp", label: "FTP / month", align: "right", render: (r) => <span className="tnum">{tk(r.ftp_change, true)}</span> },
          ]} />
        </Card>
        <Card title="Branches most affected" subtitle="Change in FTP profit a month: the ten hit hardest, then the five that gain most.">
          <Table<ScenarioGroup> rows={res.by_branch} csvName="scenario-by-branch" cols={[
            { key: "label", label: "Branch", render: (r) => <span style={{ fontWeight: 600 }}>{r.label}</span> },
            { key: "ftp", label: "FTP / month", align: "right", render: (r) => <span className="tnum">{tk(r.ftp_change, true)}</span> },
            { key: "dep", label: "Balances", align: "right", render: (r) =>
              <span className="tnum" style={muted}>{tk(Number(r.balance_new) - Number(r.balance_base), true)}</span> },
          ]} />
        </Card>
      </Grid>
    </div>
  );
}

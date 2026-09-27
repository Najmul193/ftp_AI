import { useEffect, useMemo, useRef, useState } from "react";
import { Button, Card, Grid, MiniButton, Pill, Table } from "../components/ui";
import { Icon } from "../components/icons";
import { useApp } from "../state";
import {
  aiApi, AiSettings, EgressItem, Policy, Preset, ProbeResult, Provider, Tier, TryResult,
} from "./api";
import Switch from "./Switch";

// Fields take the global form look; only the width is set here.
const field: React.CSSProperties = { width: "100%" };
const label: React.CSSProperties = {
  fontSize: "var(--fs-xs)", fontWeight: 600, letterSpacing: ".05em",
  textTransform: "uppercase", color: "var(--text-muted)", marginBottom: 4, display: "block",
};
const hint: React.CSSProperties = {
  fontSize: "var(--fs-xs)", color: "var(--text-muted)", margin: "4px 0 0", lineHeight: 1.45,
};
const para: React.CSSProperties = {
  margin: 0, fontSize: "var(--fs-base)", color: "var(--text-secondary)", lineHeight: 1.55,
};

const TIERS: { id: Tier; title: string; body: string }[] = [
  { id: "PUBLIC", title: "Market data & news only",
    body: "No bank data ever reaches this provider. Safe for any provider, including free tiers." },
  { id: "AGGREGATE", title: "Masked bank aggregates",
    body: "Branch, district and division names become tokens, amounts are rounded, account numbers never leave." },
  { id: "RESTRICTED", title: "Identifiable aggregates",
    body: "Only when the policy sends a name in clear. For on-premise models or providers under a bank contract." },
];

const FIELD_LABEL: Record<string, string> = {
  branch: "Branch names & codes", district: "District names", division: "Division names",
  branch_category: "Branch category (metro, urban, rural)",
  product_name: "Product names (generic types)", product_code: "Internal product codes",
  account_no: "Account numbers", customer: "Customer data", user: "Staff usernames",
  employee_id: "Employee IDs", udf: "Custom (UDF) fields",
};
const EXAMPLE: Record<string, [string, string]> = {
  branch: ["Dhaka Main (100)", "BR_7KQ"], district: ["Chattogram", "DIST_M2P"],
  division: ["Sylhet division", "DIV_4TX"], product_name: ["TERM DEPOSIT - 3 MONTHS", "PRD_9HC"],
  product_code: ["TDR03", "PRD_9HC"], branch_category: ["URBAN", "URBAN"],
};
const ACTION_LABEL: Record<string, string> = {
  token: "Tokenise", drop: "Remove", pass: "Send as-is",
};

const tierTone = (t: Tier) => (t === "PUBLIC" ? "good" : t === "AGGREGATE" ? "info" : "warning");

type Msg = { tone: "good" | "critical"; text: string } | null;

export default function AiAdmin() {
  const { can, me, refreshAi } = useApp();
  const [s, setS] = useState<AiSettings | null>(null);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [msg, setMsg] = useState<Msg>(null);
  const [busy, setBusy] = useState(false);
  // Bumped after a gateway call so the egress log shows the new record.
  const [egressTick, setEgressTick] = useState(0);
  const [loadError, setLoadError] = useState<string | null>(null);
  const topRef = useRef<HTMLDivElement>(null);

  const editable = can("AI_ADMIN") && me?.scope_level === "HO";

  const load = async () => {
    try {
      const [st, ps] = await Promise.all([aiApi.settings(), aiApi.presets()]);
      setS(st); setPresets(ps); setLoadError(null);
    } catch (e) { setLoadError((e as Error).message); }
  };
  useEffect(() => { load(); }, []);

  const done = (text: string) => {
    setMsg({ tone: "good", text }); load(); refreshAi();
    topRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  };
  const fail = (e: unknown) => {
    setMsg({ tone: "critical", text: (e as Error).message });
    topRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  if (loadError) return <Card><p style={para}>Could not load AI settings: {loadError}</p></Card>;
  if (!s) return <p style={{ color: "var(--text-muted)" }}>Loading…</p>;

  const active = s.providers.find((p) => p.id === s.active_provider_id) ?? null;

  const toggle = async (on: boolean) => {
    setBusy(true); setMsg(null);
    try {
      await aiApi.setEnabled(on);
      done(on ? "AI is on across the system." : "AI is off across the system. Nothing is sent to any provider.");
    } catch (e) { fail(e); } finally { setBusy(false); }
  };

  return (
    <div ref={topRef} style={{ display: "flex", flexDirection: "column", gap: 14, scrollMarginTop: 24 }}>
      {msg && (
        <div style={{
          padding: "9px 12px", borderRadius: 8, fontSize: "var(--fs-base)",
          border: `1px solid ${msg.tone === "good" ? "var(--status-good)" : "var(--status-critical)"}`,
          background: "var(--surface-2)",
        }}>
          <Pill tone={msg.tone}>{msg.tone === "good" ? "Done" : "Refused"}</Pill>
          <span style={{ marginLeft: 8, color: "var(--text-secondary)" }}>{msg.text}</span>
        </div>
      )}

      {/* --- master switch ------------------------------------------------ */}
      <Card title="AI features" subtitle="One switch for every AI feature, for every user">
        <div style={{ display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap", padding: "4px 2px" }}>
          <Switch size="lg" checked={s.enabled} disabled={!editable || busy || (!s.enabled && !active)}
                  onChange={toggle} label={s.enabled ? "Turn AI off" : "Turn AI on"} />
          <div style={{ flex: "1 1 320px", minWidth: 0 }}>
            <div style={{ fontSize: "var(--fs-md)", fontWeight: 650, color: "var(--text-primary)" }}>
              {s.enabled ? "AI is on" : "AI is off"}
            </div>
            <p style={{ ...para, marginTop: 2 }}>
              {s.enabled
                ? <>Answering through <b>{active?.label}</b> ({active?.model}). Turning it off stops every
                   request immediately, including ones already in progress.</>
                : active
                  ? <>Ready: <b>{active.label}</b> is connected. Nothing leaves the bank while AI is off.</>
                  : <>Connect a provider below and activate it to turn AI on.</>}
            </p>
          </div>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            <Pill tone={tierTone(s.bank_tier)}>Bank data leaves as {s.bank_tier.toLowerCase()}</Pill>
            {active && <Pill tone={tierTone(active.max_data_tier)}>
              Provider cleared for {active.max_data_tier.toLowerCase()}</Pill>}
          </div>
        </div>
      </Card>

      <Providers settings={s} presets={presets} editable={editable} onDone={done} onFail={fail} />
      <Connect presets={presets} editable={editable} onDone={done} onFail={fail} />
      <PolicyCard settings={s} editable={editable} onDone={done} onFail={fail} />
      <TryIt enabled={s.enabled} onSent={() => { setEgressTick((t) => t + 1); load(); }} />
      {can("AI_AUDIT") && <EgressLog tick={egressTick} />}
    </div>
  );
}

// --------------------------------------------------------------------------
// Connected providers
// --------------------------------------------------------------------------

function Providers({ settings, presets, editable, onDone, onFail }: {
  settings: AiSettings; presets: Preset[]; editable: boolean;
  onDone: (t: string) => void; onFail: (e: unknown) => void;
}) {
  const [editing, setEditing] = useState<Provider | null>(null);
  const [working, setWorking] = useState<number | null>(null);

  const run = async (id: number, f: () => Promise<string>) => {
    setWorking(id);
    try { onDone(await f()); } catch (e) { onFail(e); } finally { setWorking(null); }
  };

  const statusPill = (p: Provider) =>
    p.status === "ok" ? <Pill tone="good">Connected</Pill>
      : p.status === "failed" ? <Pill tone="critical">Failed</Pill>
        : <Pill tone="neutral">Untested</Pill>;

  return (
    <Card title="Connected providers"
          subtitle="Keys are encrypted at rest and never shown again, only their last four characters">
      <Table<Provider>
        rows={settings.providers}
        empty="No provider connected yet. Connect one below."
        cols={[
          { key: "label", label: "Provider", render: (p) => (
            <div>
              <div style={{ fontWeight: 600, display: "flex", gap: 6, alignItems: "center" }}>
                {p.label}{p.id === settings.active_provider_id && <Pill tone="info">Active</Pill>}
              </div>
              <div style={{ fontSize: "var(--fs-xs)", color: "var(--text-muted)" }}>{p.model}</div>
            </div>) },
          { key: "tier", label: "Cleared for", render: (p) => (
            <span style={{ display: "inline-flex", gap: 4, flexWrap: "wrap" }}>
              <Pill tone={tierTone(p.max_data_tier)}>{p.max_data_tier.toLowerCase()}</Pill>
              {p.is_free_tier && <Pill tone="warning">free tier</Pill>}
            </span>) },
          { key: "key", label: "Key", render: (p) => p.has_key
            ? <span className="tnum">•••• {p.key_last4}</span>
            : <span style={{ color: "var(--text-muted)" }}>none</span> },
          { key: "status", label: "Status", render: (p) => (
            <div title={p.status_detail ?? undefined}>
              {statusPill(p)}
              {p.status === "failed" && p.status_detail && (
                <div style={{ ...hint, maxWidth: 260 }}>{p.status_detail}</div>)}
            </div>) },
          { key: "used", label: "Tokens today", align: "right", render: (p) => (
            <span className="tnum">{(p.tokens_today ?? 0).toLocaleString("en-US")}
              {p.daily_token_budget > 0 && <span style={{ color: "var(--text-muted)" }}>
                {" "}/ {p.daily_token_budget.toLocaleString("en-US")}</span>}
            </span>) },
          { key: "act", label: "", align: "right", render: (p) => editable && (
            <div style={{ display: "flex", gap: 4, justifyContent: "flex-end", flexWrap: "wrap" }}>
              <MiniButton disabled={working === p.id} onClick={() => run(p.id, async () => {
                const r = await aiApi.testProvider(p.id);
                if (!r.test.ok) throw new Error(`${p.label}: ${r.test.error}`);
                return `${p.label} answered in ${r.test.latency_ms ?? "?"} ms.`;
              })}>{working === p.id ? "Testing…" : "Test"}</MiniButton>
              {p.id !== settings.active_provider_id && (
                <MiniButton disabled={p.status !== "ok" || working === p.id}
                            title={p.status !== "ok" ? "Test it successfully first" : undefined}
                            onClick={() => run(p.id, async () => {
                              await aiApi.activate(p.id); return `${p.label} is now the active provider.`;
                            })}>Activate</MiniButton>)}
              <MiniButton onClick={() => setEditing(p)}>Edit</MiniButton>
              <MiniButton onClick={() => {
                if (!window.confirm(`Remove ${p.label}? Its key is deleted.`)) return;
                run(p.id, async () => { await aiApi.deleteProvider(p.id); return `${p.label} removed.`; });
              }}>Remove</MiniButton>
            </div>) },
        ]}
      />
      {editing && (
        <EditProvider key={editing.id} p={editing}
                      preset={presets.find((x) => x.brand === editing.brand)}
                      onClose={() => setEditing(null)}
                      onDone={(t) => { setEditing(null); onDone(t); }} onFail={onFail} />
      )}
    </Card>
  );
}

function EditProvider({ p, preset, onClose, onDone, onFail }: {
  p: Provider; preset?: Preset; onClose: () => void;
  onDone: (t: string) => void; onFail: (e: unknown) => void;
}) {
  const [model, setModel] = useState(p.model);
  const [tier, setTier] = useState<Tier>(p.max_data_tier);
  const [free, setFree] = useState(p.is_free_tier);
  const [ack, setAck] = useState(false);
  const [key, setKey] = useState("");
  const [budget, setBudget] = useState(String(p.daily_token_budget));
  const [busy, setBusy] = useState(false);
  const needsAck = free && tier !== "PUBLIC" && !preset?.local && !p.trial_ack_at;

  const save = async () => {
    setBusy(true);
    try {
      await aiApi.updateProvider(p.id, {
        model, max_data_tier: tier, is_free_tier: free, trial_ack: ack,
        daily_token_budget: Number(budget) || 0, ...(key ? { api_key: key } : {}),
      });
      onDone(`${p.label} updated.${key ? " Test it before relying on the new key." : ""}`);
    } catch (e) { onFail(e); } finally { setBusy(false); }
  };

  return (
    <div style={{ borderTop: "1px solid var(--border)", marginTop: 12, paddingTop: 14,
                  display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ fontWeight: 650 }}>Edit {p.label}</div>
      <Grid cols="repeat(auto-fit, minmax(200px, 1fr))" gap={12}>
        <label><span style={label}>Model</span>
          <input style={field} value={model} list={`models-${p.id}`} onChange={(e) => setModel(e.target.value)} />
          <datalist id={`models-${p.id}`}>
            {(preset?.suggested_models ?? []).map((m) => <option key={m} value={m} />)}
          </datalist>
        </label>
        <label><span style={label}>Replace key</span>
          <input style={field} type="password" autoComplete="off" value={key}
                 placeholder={p.has_key ? `•••• ${p.key_last4}` : "none"}
                 onChange={(e) => setKey(e.target.value)} />
        </label>
        <label><span style={label}>Daily token budget</span>
          <input style={field} inputMode="numeric" value={budget}
                 onChange={(e) => setBudget(e.target.value.replace(/\D/g, ""))} />
          <p style={hint}>0 means unlimited.</p>
        </label>
      </Grid>
      <TierPicker value={tier} onChange={setTier} />
      <FreeTierControls free={free} setFree={setFree} needsAck={needsAck} ack={ack} setAck={setAck}
                        local={Boolean(preset?.local)} />
      <div style={{ display: "flex", gap: 8 }}>
        <Button variant="primary" disabled={busy || !model || (needsAck && !ack)} onClick={save}>
          {busy ? "Saving…" : "Save changes"}</Button>
        <Button onClick={onClose}>Cancel</Button>
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------
// Connect a provider
// --------------------------------------------------------------------------

function Connect({ presets, editable, onDone, onFail }: {
  presets: Preset[]; editable: boolean; onDone: (t: string) => void; onFail: (e: unknown) => void;
}) {
  const [brand, setBrand] = useState<string | null>(null);
  const preset = presets.find((p) => p.brand === brand) ?? null;
  const [baseUrl, setBaseUrl] = useState("");
  const [key, setKey] = useState("");
  const [probe, setProbe] = useState<ProbeResult | null>(null);
  const [model, setModel] = useState("");
  const [lbl, setLbl] = useState("");
  const [tier, setTier] = useState<Tier>("PUBLIC");
  const [free, setFree] = useState(false);
  const [ack, setAck] = useState(false);
  const [budget, setBudget] = useState("0");
  const [busy, setBusy] = useState<"probe" | "save" | null>(null);

  const pick = (p: Preset) => {
    setBrand(p.brand); setBaseUrl(p.base_url); setKey(""); setProbe(null);
    setModel(p.suggested_models[0] ?? ""); setLbl(""); setTier("PUBLIC");
    setFree(p.free_tier); setAck(false);
  };

  const models = useMemo(() => {
    const all = [...(probe?.models ?? []), ...(preset?.suggested_models ?? [])];
    return Array.from(new Set(all));
  }, [probe, preset]);

  if (!editable) return null;

  const connect = async () => {
    if (!preset) return;
    setBusy("probe"); setProbe(null);
    try {
      const r = await aiApi.probe({ brand: preset.brand, base_url: baseUrl || undefined,
                                    api_key: key || undefined, model: model || undefined });
      setProbe(r);
      if (r.ok && r.models.length && !r.models.includes(model)) {
        // Prefer a suggested model the provider actually offers.
        setModel(preset.suggested_models.find((m) => r.models.includes(m)) ?? r.models[0]);
      }
    } catch (e) { onFail(e); } finally { setBusy(null); }
  };

  const needsAck = free && tier !== "PUBLIC" && !preset?.local;

  const save = async () => {
    if (!preset) return;
    setBusy("save");
    try {
      const r = await aiApi.createProvider({
        brand: preset.brand, model, api_key: key || undefined, base_url: baseUrl || undefined,
        label: lbl || undefined, max_data_tier: tier, is_free_tier: free, trial_ack: ack,
        daily_token_budget: Number(budget) || 0,
      });
      setBrand(null);
      onDone(r.test.ok
        ? `${r.provider.label} connected and answering. Activate it to use it.`
        : `${r.provider.label} saved, but the test failed: ${r.test.error}`);
    } catch (e) { onFail(e); } finally { setBusy(null); }
  };

  const showUrl = preset && (preset.brand === "custom" || preset.brand === "ollama" || preset.local);

  return (
    <Card title="Connect a provider"
          subtitle="Pick a provider, paste its key, and connect. The key is checked before it is saved.">
      <div style={{ display: "grid", gap: 8,
                    gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))" }}>
        {presets.map((p) => (
          <button key={p.brand} type="button" onClick={() => pick(p)}
                  aria-pressed={brand === p.brand}
                  className={`btn ${brand === p.brand ? "btn-active" : "btn-secondary"}`}
                  style={{ textAlign: "left", padding: "10px 12px", borderRadius: "var(--radius-sm)",
                           display: "flex", flexDirection: "column", gap: 5, height: "auto" }}>
            <span style={{ fontWeight: 650, fontSize: "var(--fs-base)" }}>{p.label}</span>
            <span>{p.local ? <Pill tone="good">On-premise</Pill>
              : p.free_tier ? <Pill tone="warning">Free tier</Pill>
                : p.brand === "custom" ? <Pill tone="neutral">Any endpoint</Pill>
                  : <Pill tone="info">Paid API</Pill>}</span>
          </button>
        ))}
      </div>

      {preset && (
        <div style={{ borderTop: "1px solid var(--border)", marginTop: 14, paddingTop: 14,
                      display: "flex", flexDirection: "column", gap: 12 }}>
          <p style={para}>{preset.note}{" "}
            {preset.key_url && <a href={preset.key_url} target="_blank" rel="noreferrer">
              Get a {preset.label} key ↗</a>}</p>

          <Grid cols="repeat(auto-fit, minmax(220px, 1fr))" gap={12}>
            {showUrl && (
              <label><span style={label}>Base URL</span>
                <input style={field} value={baseUrl} placeholder="https://…/v1"
                       onChange={(e) => { setBaseUrl(e.target.value); setProbe(null); }} />
              </label>)}
            {(preset.needs_key || preset.brand === "custom") && (
              <label><span style={label}>API key{preset.needs_key ? "" : " (optional)"}</span>
                <input style={field} type="password" autoComplete="off" value={key}
                       onChange={(e) => { setKey(e.target.value); setProbe(null); }} />
              </label>)}
          </Grid>

          <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
            <Button variant="primary" icon="shield"
                    disabled={busy !== null || (preset.needs_key && !key) || !baseUrl}
                    onClick={connect}>{busy === "probe" ? "Connecting…" : "Connect"}</Button>
            {probe && (probe.ok
              ? <span style={para}><Pill tone="good">Connected</Pill>{" "}
                  {probe.models.length} model{probe.models.length === 1 ? "" : "s"} available
                  {probe.reply && <> · replied “{probe.reply}” in {probe.latency_ms} ms</>}</span>
              : <span style={para}><Pill tone="critical">Could not connect</Pill> {probe.error}</span>)}
          </div>

          {probe?.ok && (
            <>
              <Grid cols="repeat(auto-fit, minmax(220px, 1fr))" gap={12}>
                <label><span style={label}>Model</span>
                  <select style={field} value={model} onChange={(e) => setModel(e.target.value)}>
                    {models.map((m) => <option key={m} value={m}>{m}</option>)}
                  </select>
                </label>
                <label><span style={label}>Name (optional)</span>
                  <input style={field} value={lbl} placeholder={preset.label}
                         onChange={(e) => setLbl(e.target.value)} />
                </label>
                <label><span style={label}>Daily token budget</span>
                  <input style={field} inputMode="numeric" value={budget}
                         onChange={(e) => setBudget(e.target.value.replace(/\D/g, ""))} />
                  <p style={hint}>0 means unlimited.</p>
                </label>
              </Grid>
              <TierPicker value={tier} onChange={setTier} />
              <FreeTierControls free={free} setFree={setFree} needsAck={needsAck} ack={ack}
                                setAck={setAck} local={preset.local} />
              <div>
                <Button variant="primary" disabled={busy !== null || !model || (needsAck && !ack)}
                        onClick={save}>{busy === "save" ? "Saving…" : "Save and test"}</Button>
              </div>
            </>
          )}
        </div>
      )}
    </Card>
  );
}

function TierPicker({ value, onChange }: { value: Tier; onChange: (t: Tier) => void }) {
  return (
    <div>
      <span style={label}>What this provider may receive</span>
      <div role="radiogroup" style={{ display: "grid", gap: 8,
                                      gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))" }}>
        {TIERS.map((t) => (
          <button key={t.id} type="button" role="radio" aria-checked={value === t.id}
                  onClick={() => onChange(t.id)}
                  className={`btn ${value === t.id ? "btn-active" : "btn-secondary"}`}
                  style={{ textAlign: "left", padding: "10px 12px", height: "auto",
                           borderRadius: "var(--radius-sm)", display: "block" }}>
            <span style={{ display: "flex", gap: 6, alignItems: "center", fontWeight: 650 }}>
              <Pill tone={tierTone(t.id)}>{t.id.toLowerCase()}</Pill>{t.title}
            </span>
            <span style={{ ...hint, display: "block", fontWeight: 400 }}>{t.body}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

function FreeTierControls({ free, setFree, needsAck, ack, setAck, local }: {
  free: boolean; setFree: (v: boolean) => void; needsAck: boolean;
  ack: boolean; setAck: (v: boolean) => void; local: boolean;
}) {
  if (local) return null;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: "var(--fs-base)" }}>
        <input type="checkbox" checked={free} onChange={(e) => setFree(e.target.checked)} />
        This key is on a free tier (the provider's terms may allow it to use prompts)
      </label>
      {needsAck && (
        <div style={{ padding: "10px 12px", borderRadius: 8, border: "1px solid var(--status-warning)",
                      background: "var(--surface-2)", display: "flex", flexDirection: "column", gap: 8 }}>
          <span style={para}><Pill tone="warning">Trial only</Pill>{" "}
            Masked bank aggregates would go to a free tier. Names are tokenised and amounts rounded, but
            a free tier's terms are not bank-grade. Use a paid, no-training plan or an on-premise model
            before real production data.</span>
          <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: "var(--fs-base)" }}>
            <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} />
            I accept this for a trial. My name and the time are recorded.
          </label>
        </div>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------
// Data policy
// --------------------------------------------------------------------------

function PolicyCard({ settings, editable, onDone, onFail }: {
  settings: AiSettings; editable: boolean; onDone: (t: string) => void; onFail: (e: unknown) => void;
}) {
  const [p, setP] = useState<Policy>(settings.policy);
  const [busy, setBusy] = useState(false);
  useEffect(() => setP(settings.policy), [settings.policy]);
  const dirty = JSON.stringify(p) !== JSON.stringify(settings.policy);
  const clearName = ["branch", "district", "division"].some((f) => p.actions[f] === "pass");

  const save = async () => {
    setBusy(true);
    try {
      const r = await aiApi.setPolicy(p);
      onDone(`Data policy saved. Bank data now leaves as ${r.bank_tier.toLowerCase()}.`);
    } catch (e) { onFail(e); } finally { setBusy(false); }
  };

  type Row = { field: string; options: string[] | null };
  const rows: Row[] = [
    ...Object.entries(settings.policy_options).map(([f, o]) => ({ field: f, options: o })),
    ...settings.hard_drop.map((f) => ({ field: f, options: null })),
  ];

  return (
    <Card title="What may leave the bank"
          subtitle="Applied to every request before it reaches any provider, then proven by a final scan">
      <Table<Row> rows={rows} cols={[
        { key: "f", label: "Data", render: (r) => (
          <span style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
            {!r.options && <Icon name="lock" size={14} />}{FIELD_LABEL[r.field] ?? r.field}
          </span>) },
        { key: "a", label: "Leaves as", render: (r) => r.options
          ? <select value={p.actions[r.field]} disabled={!editable}
                    onChange={(e) => setP({ ...p, actions: { ...p.actions, [r.field]: e.target.value } })}>
              {r.options.map((o) => <option key={o} value={o}>{ACTION_LABEL[o] ?? o}</option>)}
            </select>
          : <Pill tone="good">Never leaves</Pill> },
        { key: "e", label: "Example", render: (r) => (
          <span style={{ color: "var(--text-muted)", fontSize: "var(--fs-sm)" }}>
            {!r.options ? "Blocked at every tier, not configurable"
              : p.actions[r.field] === "token" ? `${EXAMPLE[r.field]?.[0]} → ${EXAMPLE[r.field]?.[1]}`
              : p.actions[r.field] === "drop" ? "left out entirely"
              : `${EXAMPLE[r.field]?.[0] ?? "value"}, sent in clear`}
          </span>) },
      ]} />
      <div style={{ display: "flex", gap: 12, alignItems: "flex-end", flexWrap: "wrap", marginTop: 12 }}>
        <label style={{ minWidth: 240 }}><span style={label}>Amounts</span>
          <select style={field} value={p.amount_mode} disabled={!editable}
                  onChange={(e) => setP({ ...p, amount_mode: e.target.value as Policy["amount_mode"] })}>
            <option value="crore_3sf">Rounded to 3 figures in crore (413 cr)</option>
            <option value="index">Indexed, largest = 100 (no absolute size)</option>
          </select>
        </label>
        {clearName && <Pill tone="warning">A name is sent in clear: bank data becomes restricted</Pill>}
        {editable && <Button variant="primary" disabled={!dirty || busy} onClick={save}>
          {busy ? "Saving…" : "Save policy"}</Button>}
      </div>
    </Card>
  );
}

// --------------------------------------------------------------------------
// Try the gateway
// --------------------------------------------------------------------------

function TryIt({ enabled, onSent }: { enabled: boolean; onSent: () => void }) {
  const { can } = useApp();
  const [q, setQ] = useState("Why did Dhaka Main lose deposits while Chattogram GEC grew?");
  const [r, setR] = useState<TryResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  if (!can("AI_CHAT")) return null;

  const ask = async () => {
    setBusy(true); setErr(null); setR(null);
    try { setR(await aiApi.try(q)); } catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); onSent(); }
  };

  const panel = (title: string, body: string, tone?: "good") => (
    <div style={{ border: "1px solid var(--border)", borderRadius: "var(--radius-sm)",
                  padding: "10px 12px", background: tone ? "var(--surface-2)" : "var(--surface-1)",
                  minWidth: 0 }}>
      <span style={label}>{title}</span>
      <div style={{ fontSize: "var(--fs-base)", lineHeight: 1.55, whiteSpace: "pre-wrap",
                    overflowWrap: "anywhere", color: "var(--text-primary)" }}>{body}</div>
    </div>
  );

  return (
    <Card title="Try the gateway"
          subtitle="Ask anything. Name a branch or district to see it tokenised; paste an account number to see it stopped.">
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <textarea value={q} onChange={(e) => setQ(e.target.value)} rows={2} maxLength={2000}
                  style={{ width: "100%", resize: "vertical", font: "inherit", padding: "8px 10px" }} />
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <Button variant="primary" icon="sparkle" disabled={!enabled || busy || !q.trim()} onClick={ask}>
            {busy ? "Asking…" : "Ask"}</Button>
          {!enabled && <span style={hint}>Turn AI on to try it.</span>}
          {r && <span style={hint}>Sent as <b>{r.tier.toLowerCase()}</b> to {r.provider} ({r.model}) ·
            egress record #{r.request_id}</span>}
        </div>
        {err && <div style={para}><Pill tone="critical">Stopped</Pill> {err}</div>}
        {r && (
          <Grid cols="repeat(auto-fit, minmax(240px, 1fr))" gap={10}>
            {panel("You typed", r.you_typed)}
            {panel("What left the bank", r.sent)}
            {panel("Answer", r.answer, "good")}
          </Grid>
        )}
      </div>
    </Card>
  );
}

// --------------------------------------------------------------------------
// Egress log
// --------------------------------------------------------------------------

function EgressLog({ tick: outerTick }: { tick: number }) {
  const [status, setStatus] = useState("");
  const [items, setItems] = useState<EgressItem[]>([]);
  const [total, setTotal] = useState(0);
  const [open, setOpen] = useState<EgressItem | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    aiApi.egressLog({ limit: 50, status }).then((d) => { setItems(d.items); setTotal(d.total); })
      .catch(() => { setItems([]); setTotal(0); });
  }, [status, tick, outerTick]);

  const more = async () => {
    const d = await aiApi.egressLog({ limit: 50, offset: items.length, status });
    setItems([...items, ...d.items]);
  };

  const tone = (s: EgressItem["status"]) => (s === "ok" ? "good" : s === "blocked" ? "warning" : "critical");

  return (
    <Card title="Egress log" subtitle="Every request to a provider, exactly as it left, including the ones that were stopped"
          actions={<div style={{ display: "flex", gap: 2 }}>
            {[["", "All"], ["ok", "Sent"], ["blocked", "Stopped"], ["error", "Failed"]].map(([v, l]) => (
              <MiniButton key={v} active={status === v} onClick={() => setStatus(v)}>{l}</MiniButton>))}
            <MiniButton icon="refresh" onClick={() => setTick((t) => t + 1)}>Refresh</MiniButton>
          </div>}>
      <Table<EgressItem> rows={items} empty="Nothing has been sent yet." onRowClick={setOpen} cols={[
        { key: "t", label: "When", render: (r) => (
          <span className="tnum">{new Date(r.created_at).toLocaleString("en-GB")}</span>) },
        { key: "u", label: "User", render: (r) => r.username ?? "system" },
        { key: "p", label: "Purpose", render: (r) => r.purpose.replace(/_/g, " ") },
        { key: "tier", label: "Data", render: (r) => <Pill tone={tierTone(r.tier)}>{r.tier.toLowerCase()}</Pill> },
        { key: "s", label: "Outcome", render: (r) => (
          <span title={r.blocked_reason ?? undefined}>
            <Pill tone={tone(r.status)}>{r.status === "ok" ? "sent" : r.status === "blocked" ? "stopped" : "failed"}</Pill>
          </span>) },
        { key: "why", label: "Detail", render: (r) => (
          <span style={{ color: "var(--text-muted)", fontSize: "var(--fs-sm)" }}>
            {r.blocked_reason ?? `${r.provider ?? ""} · ${r.latency_ms ?? "–"} ms`}
          </span>) },
      ]} />
      {items.length < total && (
        <div style={{ marginTop: 10 }}><MiniButton onClick={more}>Load more ({total - items.length})</MiniButton></div>)}
      {open && (
        <div style={{ borderTop: "1px solid var(--border)", marginTop: 12, paddingTop: 12,
                      display: "flex", flexDirection: "column", gap: 10 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <b>Record #{open.id}</b><MiniButton onClick={() => setOpen(null)}>Close</MiniButton>
          </div>
          <Grid cols="repeat(auto-fit, minmax(280px, 1fr))" gap={10}>
            <Pre title="Instructions" body={open.payload.system} />
            {open.payload.messages.map((m, i) => (
              <Pre key={i} title={`${m.role} · ${m.segments.join(", ")}`} body={m.content} />))}
            {open.response && <Pre title="Provider's reply (as received)" body={open.response} />}
          </Grid>
        </div>
      )}
    </Card>
  );
}

function Pre({ title, body }: { title: string; body: string }) {
  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: "var(--radius-sm)",
                  padding: "10px 12px", minWidth: 0 }}>
      <span style={label}>{title}</span>
      <pre style={{ margin: 0, whiteSpace: "pre-wrap", overflowWrap: "anywhere",
                    fontSize: "var(--fs-sm)", lineHeight: 1.5 }}>{body}</pre>
    </div>
  );
}

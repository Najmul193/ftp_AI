import { useMemo } from "react";
import Chart, { axisCommon, baseOption, useTokens } from "../components/Chart";
import { Table } from "../components/ui";
import { compact, shortDate } from "../format";
import { AskResult, ResultColumn, taka } from "./api";
import Cone from "./Cone";

type Row = Record<string, string | number | null>;

const num = (v: unknown) => (v == null || v === "" ? null : Number(v));

/** A cell as a person reads it. Exact values; the chart may abbreviate. */
export function cell(v: unknown, unit: string, row?: Row): string {
  if (v == null || v === "") return "—";
  if (unit === "mixed") unit = row?.unit === "pct" ? "pct" : row?.unit === "bdt" ? "bdt" : "num";
  if (unit === "mixed_change") unit = row?.unit === "pct" ? "pp" : row?.unit === "bdt" ? "bdt" : "num";
  const n = Number(v);
  switch (unit) {
    case "bdt": return taka(n);
    case "pct": return `${n.toFixed(2)}%`;
    case "pp": { const b = Math.round(n * 100); return `${b > 0 ? "+" : ""}${b} bp`; }
    case "num": return n.toLocaleString("en-IN", { maximumFractionDigits: 2 });
    case "count": return n.toLocaleString("en-IN");
    case "date": return typeof v === "string" && /^\d{4}-\d{2}-\d{2}$/.test(v) ? shortDate(v) : String(v);
    default: return String(v);
  }
}

function axisLabel(unit: string) {
  return (v: number) => unit === "bdt" ? compact(v) : unit === "pp" ? `${Math.round(v * 100)} bp`
    : unit === "pct" ? `${v}%` : String(v);
}

function ResultChart({ r, height }: { r: AskResult; height: number }) {
  const t = useTokens();
  const spec = r.chart!;
  const option = useMemo(() => {
    // Top 15 bars at most; the table has the rest.
    const rows = spec.type === "bar" ? r.rows.slice(0, 15) : r.rows;
    const cats = rows.map((x) => spec.x === "label" && spec.type === "line"
      ? cell(x.label, "date") : String(x.label ?? ""));
    const unit = spec.series[0]?.unit ?? "num";
    const fmt = (v: number) => cell(v, unit);
    const value = { type: "value" as const, ...axisCommon(t), axisLine: { show: false },
                    axisLabel: { color: t.muted, fontSize: 11, formatter: axisLabel(unit), hideOverlap: true } };
    const category = { type: "category" as const, data: cats, ...axisCommon(t),
                       splitLine: { show: false }, inverse: spec.horizontal,
                       axisLabel: { color: t.muted, fontSize: 11, width: 150, overflow: "truncate" as const } };
    const series = spec.series.map((s, i) => ({
      name: s.label, type: spec.type,
      data: rows.map((x) => num(x[s.key])),
      stack: spec.stack ? "all" : undefined,
      itemStyle: { color: t.series[i], borderRadius: spec.horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0],
                   borderColor: t.surface, borderWidth: spec.stack ? 1 : 0 },
      lineStyle: { width: 2 }, symbol: "circle", symbolSize: rows.length > 40 ? 0 : 6,
      barMaxWidth: 22,
    }));
    return {
      ...baseOption(t),
      grid: { left: 8, right: 20, top: spec.series.length > 1 ? 34 : 14, bottom: 8, containLabel: true },
      legend: spec.series.length > 1 ? { ...baseOption(t).legend, top: 0, left: 0 } : { show: false },
      tooltip: { ...baseOption(t).tooltip, trigger: "axis" as const,
                 valueFormatter: (v: unknown) => fmt(Number(v)) },
      xAxis: spec.horizontal ? value : category,
      yAxis: spec.horizontal ? category : value,
      series,
    } as never;
  }, [r, spec, t]);
  const rows = spec.type === "bar" ? Math.min(r.rows.length, 15) : 0;
  const h = spec.horizontal ? Math.max(height, rows * 26 + 50) : height;
  return <Chart option={option} height={h} ariaLabel={r.title} />;
}

export default function AnswerView({ r, compact: small = false }: { r: AskResult; compact?: boolean }) {
  const cols = r.columns.filter((c) => !(small && c.key.endsWith("__prior")));
  const tableRows: Row[] = r.total ? [...r.rows, { ...r.total, label: "All selected", __total: 1 }] : r.rows;
  const hideTable = small && r.chart != null;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {r.chart?.type === "cone" && r.chart.history && r.chart.forecast
        ? <Cone history={r.chart.history} forecast={r.chart.forecast} unit={r.chart.unit ?? "num"}
                height={small ? 160 : 200} ariaLabel={`${r.title}: history and forecast`} />
        : r.chart && r.rows.length > 0 && <ResultChart r={r} height={small ? 180 : 240} />}
      {!hideTable && (
        <Table<Row> rows={tableRows} maxHeight={small ? 220 : 320}
          csvName={small ? undefined : `${r.title.replace(/[^\w]+/g, "-").toLowerCase()}.csv`}
          empty="Nothing matched."
          cols={cols.map((c: ResultColumn) => ({
            key: c.key, label: c.label, align: c.unit === "text" || c.unit === "date" ? "left" : "right",
            value: (x: Row) => x[c.key],
            render: (x: Row) => (
              <span className="tnum" style={{ fontWeight: x.__total ? 650 : undefined,
                                              whiteSpace: c.unit === "text" ? "normal" : "nowrap" }}>
                {cell(x[c.key], c.unit, x)}</span>),
          }))} />)}
      {r.link && !small && (
        <a href={r.link.href} style={{ fontSize: "var(--fs-sm)", fontWeight: 600 }}>{r.link.label} →</a>)}
      {r.notes.length > 0 && (
        <ul style={{ margin: 0, paddingLeft: 18, fontSize: "var(--fs-sm)", color: "var(--text-muted)" }}>
          {r.notes.map((n) => <li key={n}>{n}</li>)}
        </ul>)}
    </div>
  );
}

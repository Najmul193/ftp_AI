import { useMemo } from "react";
import Chart, { axisCommon, baseOption, useTokens } from "../components/Chart";
import { compact, shortDate } from "../format";
import type { Band, Point } from "./api";

/** Value as a person reads it: taka compact, rates to 2 dp. */
export const fmtUnit = (v: number | null | undefined, unit: string) => {
  if (v == null || !Number.isFinite(Number(v))) return "—";
  const x = Number(v);
  if (unit === "pct") return `${x.toFixed(2)}%`;
  if (unit === "bdt") return `৳${compact(x)}`;
  if (unit === "usd") return `$${x.toFixed(2)}`;
  return x.toFixed(2);
};

/**
 * History, then the forecast as a cone: the likely range (P10–P90) shaded,
 * the central path drawn lighter than history so the eye can tell what
 * happened from what is expected. `extra` overlays a second forecast (a
 * scenario) on the same axes.
 */
export default function Cone({ history, forecast, unit, height = 220, ariaLabel, extra, compactAxis }: {
  history: Point[]; forecast: Band[]; unit: string; height?: number; ariaLabel: string;
  extra?: { name: string; points: { date: string; value: number }[] };
  compactAxis?: boolean;
}) {
  const t = useTokens();
  const option = useMemo(() => {
    const dates = [...history.map((p) => p.date), ...forecast.map((p) => p.date)];
    const hi = history.length;
    const pad = (arr: (number | null)[], before: number) => [...Array(before).fill(null), ...arr];
    const hist = history.map((p) => Number(p.value));
    // Join the cone to the last real value so there is no gap.
    const last = hist.length ? hist[hist.length - 1] : null;
    const lo = pad([last, ...forecast.map((p) => Number(p.p10))], hi - 1);
    const width = pad([0, ...forecast.map((p) => Number(p.p90) - Number(p.p10))], hi - 1);
    const mid = pad([last, ...forecast.map((p) => Number(p.p50))], hi - 1);
    const values = [...hist, ...forecast.flatMap((p) => [Number(p.p10), Number(p.p90)])];
    const mn = Math.min(...values), mx = Math.max(...values);
    const span = mx - mn || Math.abs(mx) * 0.01 || 1;
    const fmt = (v: number) => (unit === "pct" ? `${v.toFixed(2)}%` : unit === "bdt" ? compact(v)
      : v.toFixed(2));
    const extraSeries = extra ? [{
      name: extra.name, type: "line" as const, symbol: "none",
      data: dates.map((d) => extra.points.find((p) => p.date === d)?.value ?? null),
      connectNulls: true, lineStyle: { width: 2, color: t.series[2] }, itemStyle: { color: t.series[2] },
    }] : [];
    return {
      ...baseOption(t),
      grid: { left: 4, right: 12, top: 12, bottom: 4, containLabel: true },
      legend: { show: false },
      tooltip: {
        ...baseOption(t).tooltip, trigger: "axis" as const,
        formatter: (raw: unknown) => {
          const ps = raw as { dataIndex: number }[];
          const i = ps[0]?.dataIndex ?? 0;
          const d = dates[i];
          if (i < hi) return `<b>${shortDate(d)}</b><br/>${fmtUnit(hist[i], unit)}`;
          const f = forecast[i - hi];
          const ex = extra?.points.find((p) => p.date === d);
          return `<b>${shortDate(d)}</b> · forecast<br/>Likely ${fmtUnit(f.p50, unit)}` +
            `<br/>Range ${fmtUnit(f.p10, unit)} – ${fmtUnit(f.p90, unit)}` +
            (ex ? `<br/>${extra!.name}: ${fmtUnit(ex.value, unit)}` : "");
        },
      },
      xAxis: { type: "category" as const, data: dates.map(shortDate), boundaryGap: false,
               ...axisCommon(t), splitLine: { show: false },
               axisLabel: { color: t.muted, fontSize: 10, hideOverlap: true } },
      yAxis: { type: "value" as const, min: mn - span * 0.08, max: mx + span * 0.08, scale: true,
               ...axisCommon(t), axisLine: { show: false },
               axisLabel: { color: t.muted, fontSize: 10, formatter: fmt, show: !compactAxis } },
      series: [
        { name: "Actual", type: "line" as const, data: [...hist, ...Array(forecast.length).fill(null)],
          symbol: "none", lineStyle: { width: 2, color: t.series[0] }, itemStyle: { color: t.series[0] } },
        { name: "low", type: "line" as const, data: lo, stack: "band", symbol: "none",
          lineStyle: { opacity: 0 }, silent: true, tooltip: { show: false } },
        { name: "Likely range", type: "line" as const, data: width, stack: "band", symbol: "none",
          lineStyle: { opacity: 0 }, areaStyle: { color: t.series[0], opacity: 0.16 }, silent: true },
        { name: "Forecast", type: "line" as const, data: mid, symbol: "none",
          lineStyle: { width: 2, color: t.series[0], opacity: 0.55 } },
        ...extraSeries,
      ],
    } as never;
  }, [history, forecast, unit, t, extra, compactAxis]);
  return <Chart option={option} height={height} ariaLabel={ariaLabel} />;
}

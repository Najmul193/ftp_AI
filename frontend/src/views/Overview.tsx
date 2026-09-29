import { useMemo } from "react";
import { api } from "../api";
import Chart, { axisCommon, baseOption, useTokens } from "../components/Chart";
import { waterfallOption } from "../components/waterfall";
import {
  Card, Empty, Grid, Pill, Stat, Table, ViewToggle, useView,
} from "../components/ui";
import type { Col } from "../components/ui";
import { compact, longDate, money, n, pct, shortDate, signed } from "../format";
import { useApp, useAsync } from "../state";
import { useWhy } from "../ai/why";

export default function Overview() {
  // "Why?" buttons exist only while the optional AI module is on.
  const why = useWhy();
  const { filters, setFilters } = useApp();
  const t = useTokens();

  const summary = useAsync(() => api.summary(filters), [filters]);
  const trend = useAsync(() => api.trend(filters), [filters]);
  const branches = useAsync(() => api.byBranch(filters), [filters]);
  const waterfall = useAsync(() => api.waterfall(filters), [filters]);
  const bs = useAsync(() => api.balanceSheet(filters), [filters]);

  const [trendView, setTrendView] = useView();
  const [branchView, setBranchView] = useView();
  const [wfView, setWfView] = useView();

  const s = summary.data;
  // A delta against an empty prior window is not a 100% rise, it is a missing
  // comparison. Showing one would invent a trend that does not exist.
  const comparable = Boolean(s?.comparison?.prior_has_data);
  const d = comparable ? s?.comparison?.deltas : undefined;
  const priorNote = s?.comparison
    ? (comparable
        ? `vs prior ${s.comparison.prior_period.days}d`
        : "no earlier data to compare")
    : undefined;
  const points = trend.data?.points ?? [];
  const spark = points.map((p) => n(p.net_ftp_profit));

  // ---- trend: net FTP + moving average. Two series, ONE axis. -------------
  const trendOption = useMemo(() => {
    const dates = points.map((p) => p.key as string);
    return {
      ...baseOption(t),
      // Right padding leaves room for the end label, which otherwise clips.
      grid: { left: 8, right: 56, top: 34, bottom: 4, containLabel: true },
      legend: { ...baseOption(t).legend, top: 0, left: 0 },
      tooltip: {
        ...baseOption(t).tooltip,
        trigger: "axis",
        axisPointer: { type: "line", lineStyle: { color: t.axis, width: 1 } },
        formatter: (ps: never) => {
          const arr = ps as unknown as { axisValue: string; seriesName: string;
            value: number; color: string }[];
          const i = dates.indexOf(arr[0].axisValue);
          const p = points[i];
          return `<b>${longDate(arr[0].axisValue)}</b><br/>` +
            arr.map((x) => `<span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:${x.color};margin-right:6px"></span>${x.seriesName}: <b>${money(x.value)}</b>`).join("<br/>") +
            (p?.day_over_day != null
              ? `<br/><span style="color:${t.muted}">vs prior day: ${signed(p.day_over_day)}</span>` : "");
        },
      },
      xAxis: { type: "category", data: dates, boundaryGap: false,
               ...axisCommon(t), splitLine: { show: false },
               axisLabel: { color: t.muted, fontSize: 11,
                            formatter: (v: string) => shortDate(v) } },
      yAxis: { type: "value", ...axisCommon(t), axisLine: { show: false },
               // Fitted, not zero-anchored: these values sit in a narrow band
               // and a zero baseline would render every day as the same height.
               scale: true,
               axisLabel: { color: t.muted, fontSize: 11,
                            formatter: (v: number) => compact(v) } },
      series: [
        {
          name: "Net FTP profit", type: "line", data: points.map((p) => n(p.net_ftp_profit)),
          smooth: false, symbol: "circle", symbolSize: 8, showSymbol: points.length <= 40,
          lineStyle: { width: 2, color: t.series[0] },
          itemStyle: { color: t.series[0], borderColor: t.surface, borderWidth: 2 },
          // Direct-label the endpoint only -- a number on every point is chaos.
          endLabel: { show: true, color: t.textSecondary, fontSize: 11,
                      formatter: (p: { value: number }) => compact(p.value) },
          emphasis: { focus: "series" },
        },
        {
          name: `${Math.min(7, points.length)}-day moving average`, type: "line",
          data: points.map((p) => n(p.moving_average)),
          smooth: true, symbol: "none",
          lineStyle: { width: 2, color: t.series[1], type: "solid" },
          itemStyle: { color: t.series[1] },
          emphasis: { focus: "series" },
        },
      ],
    } as never;
  }, [points, t]);

  // ---- branch bar: ONE series, ONE colour. Not a value ramp. -------------
  const branchOption = useMemo(() => {
    const rows = [...(branches.data ?? [])].sort(
      (a, b) => n(a.net_ftp_profit) - n(b.net_ftp_profit));
    return {
      ...baseOption(t),
      grid: { left: 8, right: 60, top: 12, bottom: 4, containLabel: true },
      legend: { show: false },
      tooltip: {
        ...baseOption(t).tooltip, trigger: "item",
        formatter: (p: never) => {
          const x = p as unknown as { name: string; value: number };
          const r = rows.find((q) => q.label === x.name);
          return `<b>Branch ${x.name}</b><br/>Net FTP: <b>${money(x.value)}</b><br/>` +
            `<span style="color:${t.muted}">Balance ${compact(r?.total_balance)} · ` +
            `${r?.account_count ?? 0} account-days · avg rate ${r?.avg_ftp_rate ?? "—"}%</span>`;
        },
      },
      xAxis: { type: "value", ...axisCommon(t), axisLine: { show: false },
               axisLabel: { color: t.muted, fontSize: 11,
                            formatter: (v: number) => compact(v) } },
      yAxis: { type: "category", data: rows.map((r) => r.label), ...axisCommon(t),
               splitLine: { show: false },
               axisLabel: { color: t.textSecondary, fontSize: 12 } },
      series: [{
        type: "bar", name: "Net FTP profit",
        data: rows.map((r) => n(r.net_ftp_profit)),
        barWidth: "56%",
        // Diverging by sign so a loss-making branch is unmistakable; every
        // positive bar is the same hue, because size is already encoded by length.
        itemStyle: {
          color: (p: { value: number }) => (p.value < 0 ? t.critical : t.series[0]),
          borderRadius: [0, 4, 4, 0],
        },
        label: { show: true, position: "right", color: t.textSecondary,
                 fontSize: 11, formatter: (p: { value: number }) => compact(p.value) },
        emphasis: { itemStyle: { opacity: .85 } },
      }],
    } as never;
  }, [branches.data, t]);

  // ---- spread waterfall ---------------------------------------------------
  const wfOption = useMemo(() => {
    const comps = waterfall.data?.components ?? [];
    if (!comps.length) return null;
    // Earnings first (largest first), then deductions (largest first): the
    // waterfall rises to a peak and steps down to the net. Leading with a
    // negative would dip below zero and recover -- accurate, but far harder to
    // follow -- and ordering deductions smallest-first buries the one that
    // actually moved the number.
    const adds = comps.filter((c) => n(c.amount) >= 0)
                      .sort((a, b) => n(b.amount) - n(a.amount));
    const subs = comps.filter((c) => n(c.amount) < 0)
                      .sort((a, b) => n(a.amount) - n(b.amount));
    const ordered = [...adds, ...subs];
    return waterfallOption([
      ...ordered.map((c) => ({ label: c.label, value: n(c.amount), kind: "delta" as const })),
      { label: "Net FTP", value: n(waterfall.data?.net_ftp_profit), kind: "total" as const },
    ], t);
  }, [waterfall.data, t]);

  if (summary.error) return <Empty title="Could not load" hint={summary.error} />;

  const trendCols: Col<(typeof points)[number]>[] = [
    { key: "key", label: "Date", render: (r) => longDate(r.key as string),
      value: (r) => r.key },
    { key: "net", label: "Net FTP", align: "right",
      render: (r) => money(r.net_ftp_profit), value: (r) => r.net_ftp_profit },
    { key: "ma", label: "Moving avg", align: "right",
      render: (r) => money(r.moving_average), value: (r) => r.moving_average },
    { key: "dod", label: "Day/day", align: "right",
      render: (r) => (r.day_over_day == null ? "—" : signed(r.day_over_day)),
      value: (r) => r.day_over_day },
    { key: "cum", label: "Cumulative", align: "right",
      render: (r) => money(r.cumulative), value: (r) => r.cumulative },
  ];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <Grid cols="repeat(auto-fit, minmax(160px, 1fr))">
        <Stat label="Net FTP profit" value={money(s?.net_ftp_profit)}
              delta={d?.net_ftp_profit?.change} deltaPct={d?.net_ftp_profit?.change_pct}
              spark={spark} hint={priorNote} why={why("net_ftp_profit")} />
        <Stat label="Asset FTP" value={money(s?.asset_ftp_profit)}
              delta={d?.asset_ftp_profit?.change} deltaPct={d?.asset_ftp_profit?.change_pct}
              why={why("asset_ftp_profit")} />
        <Stat label="Liability FTP" value={money(s?.liability_ftp_profit)}
              delta={d?.liability_ftp_profit?.change}
              deltaPct={d?.liability_ftp_profit?.change_pct} why={why("liability_ftp_profit")} />
        <Stat label="FTP / balance" value={pct(s?.ftp_over_balance_pct, 4)}
              hint="annualised" why={why("ftp_yield")} />
        <Stat label="Total balance" value={compact(s?.total_balance)}
              delta={d?.total_balance?.change ? compact(d.total_balance.change) : undefined}
              deltaPct={d?.total_balance?.change_pct} hint="balance-days" />
        <Stat label="Negative FTP" value={String(s?.negative_ftp_count ?? 0)}
              tone={(s?.negative_ftp_count ?? 0) > 0 ? "bad" : "neutral"}
              hint={`of ${(s?.account_count ?? 0).toLocaleString()} account-days`} />
      </Grid>

      <Grid cols="minmax(0, 1.55fr) minmax(0, 1fr)">
        <Card expandable title="Daily FTP profit"
              subtitle={trend.data?.summary
                ? `${trend.data.summary.days} days · mean ${money(trend.data.summary.mean_daily)} · volatility ${trend.data.summary.volatility_pct ?? "—"}%`
                : undefined}
              actions={<ViewToggle view={trendView} setView={setTrendView} />}
              footnote={trend.data?.summary
                ? `Best ${longDate(trend.data.summary.best_day.date)} at ${money(trend.data.summary.best_day.value)}; worst ${longDate(trend.data.summary.worst_day.date)} at ${money(trend.data.summary.worst_day.value)}. Annualised run rate ${compact(trend.data.summary.annualised_run_rate)}.`
                : undefined}>
          {points.length === 0
            ? <Empty title="No data in this window" hint="Widen the date range or clear a filter." />
            : trendView === "chart"
              ? <Chart option={trendOption} height={286} loading={trend.loading}
                       ariaLabel="Daily net FTP profit with moving average" />
              : <Table cols={trendCols} rows={points} maxHeight={286} csvName="ftp-daily-trend.csv" />}
        </Card>

        <Card expandable title="Where the margin comes from"
              subtitle="FTP profit decomposed into its rate components"
              actions={<ViewToggle view={wfView} setView={setWfView} />}
              footnote="Each component is signed for its side of the balance sheet, so the four sum exactly to net FTP at any grain.">
          {!wfOption
            ? <Empty title="No data" />
            : wfView === "chart"
              ? <Chart option={wfOption} height={286} loading={waterfall.loading}
                       ariaLabel="Spread decomposition waterfall" />
              : <Table csvName="ftp-spread-components.csv"
                       cols={[
                         { key: "label", label: "Component" },
                         { key: "amount", label: "Amount", align: "right",
                           render: (r: never) => money((r as { amount: string }).amount),
                           value: (r: never) => (r as { amount: string }).amount },
                         { key: "rate", label: "On book", align: "right",
                           render: (r: never) => (r as { rate: string }).rate,
                           value: (r: never) => (r as { rate: string }).rate },
                       ]}
                       rows={(waterfall.data?.components ?? []) as never[]} />}
        </Card>
      </Grid>

      <Grid cols="minmax(0, 1fr) minmax(0, 1fr)">
        <Card expandable title="Net FTP profit by branch"
              subtitle="Click a bar to filter the whole page to that branch"
              actions={<ViewToggle view={branchView} setView={setBranchView} />}>
          {(branches.data ?? []).length === 0
            ? <Empty title="No branches in this slice" />
            : branchView === "chart"
              ? <Chart option={branchOption} height={Math.max(220, (branches.data?.length ?? 0) * 38 + 40)}
                       loading={branches.loading}
                       ariaLabel="Net FTP profit by branch"
                       onSelect={(name) => {
                         const b = (branches.data ?? []).find((x) => x.label === name);
                         if (b) setFilters((f) => ({ ...f, branch_id: [Number(b.key)] }));
                       }} />
              : <Table csvName="ftp-by-branch.csv" rows={branches.data ?? []}
                       cols={[
                         { key: "label", label: "Branch" },
                         { key: "net", label: "Net FTP", align: "right",
                           render: (r) => money(r.net_ftp_profit), value: (r) => r.net_ftp_profit },
                         { key: "asset", label: "Asset", align: "right",
                           render: (r) => money(r.asset_ftp_profit), value: (r) => r.asset_ftp_profit },
                         { key: "liab", label: "Liability", align: "right",
                           render: (r) => money(r.liability_ftp_profit), value: (r) => r.liability_ftp_profit },
                         { key: "rate", label: "Avg rate", align: "right",
                           render: (r) => pct(r.avg_ftp_rate, 4), value: (r) => r.avg_ftp_rate },
                       ]} />}
        </Card>

        <Card title="Balance sheet structure"
              subtitle="Asset and liability sides, and the gap between them">
          {!bs.data?.asset || !bs.data?.liability
            ? <Empty title="No data" />
            : (
              <div style={{ display: "flex", flexDirection: "column", gap: 10, padding: "4px 4px 0" }}>
                {([["Asset", bs.data.asset, t.series[0]],
                   ["Liability", bs.data.liability, t.series[2]]] as const).map(([name, v, color]) => {
                  const total = n(bs.data!.asset!.balance) + n(bs.data!.liability!.balance);
                  const w = total ? (n(v.balance) / total) * 100 : 0;
                  return (
                    <div key={name}>
                      <div style={{ display: "flex", justifyContent: "space-between",
                                    fontSize: "var(--fs-base)", marginBottom: 4 }}>
                        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                          <span aria-hidden style={{ width: 9, height: 9, borderRadius: 2,
                                                     background: color, display: "inline-block" }} />
                          <b style={{ color: "var(--text-primary)" }}>{name}</b>
                          <span style={{ color: "var(--text-muted)" }}>
                            cust {pct(v.avg_customer_rate)} · FTP {pct(v.avg_ftp_rate)}
                          </span>
                        </span>
                        <span className="tnum" style={{ color: "var(--text-secondary)" }}>
                          {compact(v.balance)}
                        </span>
                      </div>
                      <div style={{ height: 10, background: "var(--surface-sunken)",
                                    borderRadius: 5, overflow: "hidden" }}>
                        <div style={{ width: `${w}%`, height: "100%", background: color,
                                      borderRadius: 5 }} />
                      </div>
                    </div>
                  );
                })}
                <dl style={{ margin: "8px 0 0", display: "grid",
                             gridTemplateColumns: "1fr auto", rowGap: 6, fontSize: "var(--fs-base)" }}>
                  <dt style={{ color: "var(--text-muted)" }}>Funding gap</dt>
                  <dd className="tnum" style={{ margin: 0, textAlign: "right" }}>
                    {compact(bs.data.funding_gap)}
                  </dd>
                  <dt style={{ color: "var(--text-muted)" }}>Deposit coverage</dt>
                  <dd className="tnum" style={{ margin: 0, textAlign: "right" }}>
                    {bs.data.coverage_ratio ?? "—"}
                    {n(bs.data.coverage_ratio) < 1 && (
                      <span style={{ marginLeft: 6 }}>
                        <Pill tone="warning">under-funded</Pill>
                      </span>
                    )}
                  </dd>
                  <dt style={{ color: "var(--text-muted)" }}>FTP spread</dt>
                  <dd className="tnum" style={{ margin: 0, textAlign: "right" }}>
                    {pct(bs.data.ftp_spread)}
                  </dd>
                </dl>
              </div>
            )}
        </Card>
      </Grid>
    </div>
  );
}

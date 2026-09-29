"""Run a plan. Read-only, in the asker's scope, on the aggregate tables only.

Every bank query goes through the platform's own scope machinery
(`DashboardRepo._scope_for`): asking about a branch outside your scope raises
`ScopeViolation`, exactly as it does on the dashboards, and the copilot says
so rather than answering. Nothing here reads the account-level fact table.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.context import fact_sheet as sheet
from app.ai.copilot.catalog import METRICS, Plan, prior, window
from app.ai.copilot.result import (
    Column, Result, change_facts, chart_for, compute, metric_columns, passes,
)
from app.ai.insights import engine
from app.ai.market import catalog as market_catalog
from app.ai.market import service as market
from app.domain.scope import ScopeFilter
from app.domain.types import Side
from app.models import AggDailyBranch, AggDailyBranchProduct, Branch, District, Product
from app.repositories.analytics import AnalyticsRepo
from app.repositories.dashboard import Filters, _apply_filters, _apply_scope, _sums


class ToolRefused(Exception):
    """The plan is valid but cannot be answered for this person or this data."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


@dataclass(frozen=True)
class Where:
    """A plan's filters, resolved to the platform's keys."""
    branch_codes: tuple[str, ...] = ()
    division_id: int | None = None
    district_code: str | None = None
    product_codes: tuple[str, ...] = ()
    side: str | None = None
    category: str | None = None

    def to_dict(self) -> dict:
        return {"branch_codes": list(self.branch_codes), "division_id": self.division_id,
                "district_code": self.district_code, "product_codes": list(self.product_codes),
                "side": self.side, "category": self.category}

    @classmethod
    def from_dict(cls, d: dict) -> "Where":
        return cls(tuple(d.get("branch_codes") or ()), d.get("division_id"),
                   d.get("district_code"), tuple(d.get("product_codes") or ()),
                   d.get("side"), d.get("category"))

    def filters(self, db: Session, start: date, end: date) -> Filters:
        ids = list(db.scalars(select(Branch.id).where(Branch.branch_code.in_(self.branch_codes)))) \
            if self.branch_codes else []
        dist = db.scalar(select(District.id).where(District.code == self.district_code)) \
            if self.district_code else None
        return Filters(date_from=start, date_to=end, branch_ids=ids, division_id=self.division_id,
                       district_id=dist, branch_category=self.category,
                       product_codes=list(self.product_codes),
                       side=Side(self.side) if self.side else None)

    def describe(self) -> str:
        """In words, with placeholders for entities."""
        bits = []
        if self.branch_codes:
            bits.append(", ".join(f"{{BR:{c}}}" for c in self.branch_codes))
        if self.district_code:
            bits.append(f"{{DIST:{self.district_code}}}")
        if self.division_id:
            bits.append(f"{{DIV:{self.division_id}}}")
        if self.product_codes:
            bits.append(", ".join(f"{{PRD:{c}}}" for c in self.product_codes))
        if self.side:
            bits.append("loans only" if self.side == "ASSET" else "deposits only")
        if self.category:
            bits.append(f"{self.category.replace('_', '-').lower()} branches")
        return ", ".join(bits)


# --- rollups -------------------------------------------------------------------- #

def _rollup(a: AnalyticsRepo, f: Filters, by: str) -> dict:
    """Summed measures per key of `by` ("total" has the single key None)."""
    m = AggDailyBranchProduct if by == "product" or f.needs_product_grain else AggDailyBranch
    col = {"branch": m.branch_code, "product": getattr(m, "product_code", None),
           "division": m.division_id, "district": m.district_id,
           "category": m.branch_category, "date": m.business_date, "total": None}[by]
    cols = [col] if col is not None else []
    stmt = _apply_filters(_apply_scope(select(*cols, *_sums(m)), m, a.dash._scope_for(f)), m, f)
    if cols:
        stmt = stmt.group_by(col)
    out = {}
    for r in a.s.execute(stmt):
        meas = dict(r._mapping)
        key = r[0] if cols else None
        key = getattr(key, "value", key)
        out[key] = meas
    return out


#: Shared with the branch coach, which rolls every branch up the same way.
rollup = _rollup


def _labeller(db: Session, by: str, names: dict[str, str]):
    """key -> (display label, entity placeholder or None)."""
    if by == "branch":
        return lambda k: (names.get(f"BR:{k}", k), f"{{BR:{k}}}")
    if by == "product":
        return lambda k: (names.get(f"PRD:{k}", k), f"{{PRD:{k}}}")
    if by == "division":
        return lambda k: (names.get(f"DIV:{k}", str(k)), f"{{DIV:{k}}}")
    if by == "district":
        codes = {d.id: d.code for d in db.scalars(select(District))}
        return lambda k: (names.get(f"DIST:{codes.get(k, k)}", str(k)), f"{{DIST:{codes.get(k, k)}}}")
    if by == "category":
        return lambda k: (str(k).replace("_", " ").title(), None)
    if by == "date":
        return lambda k: (k.isoformat() if hasattr(k, "isoformat") else str(k), None)
    return lambda k: ("All selected", None)


def _days(a: AnalyticsRepo, f: Filters) -> int:
    return len(_rollup(a, f, "date"))


def _sort(rows: list[dict], plan: Plan) -> list[dict]:
    key = plan.sort or (f"change:{plan.where[0].metric}" if any(c.op.startswith("change")
                                                                for c in plan.where)
                        else plan.metrics[0])
    k = f"{key[7:]}__change" if key.startswith("change:") else key
    present = [r for r in rows if r.get(k) is not None]
    missing = [r for r in rows if r.get(k) is None]
    return sorted(present, key=lambda r: r[k], reverse=plan.order == "desc") + missing


def _period(start, end, pstart=None, pend=None) -> dict:
    d = {"start": start, "end": end}
    if pstart:
        d.update(prior_start=pstart, prior_end=pend)
    return d


def _span(start: date, end: date) -> str:
    return f"{start:%d %b}" if start == end else f"{start:%d %b}–{end:%d %b %Y}"


# --- tools ---------------------------------------------------------------------- #

def compare(db: Session, a: AnalyticsRepo, plan: Plan, where: Where, start: date, end: date,
            names: dict[str, str]) -> Result:
    f = where.filters(db, start, end)
    now = _rollup(a, f, plan.by)
    days = _days(a, f)
    was: dict = {}
    pdays = 0
    ps = pe = None
    no_prior_note = None
    if plan.compare:
        ps, pe = prior(start, end)
        pf = where.filters(db, ps, pe)
        was, pdays = _rollup(a, pf, plan.by), _days(a, pf)
        span = (end - start).days + 1
        if not was and span >= 2:
            # Nothing before the period (it starts where the data does): compare
            # its second half with its first, as the profit bridge does, rather
            # than answer "did it fall?" with no comparison at all.
            half = span // 2
            ps, pe = start, start + timedelta(days=half - 1)
            start = start + timedelta(days=half)
            f = where.filters(db, start, end)
            now, days = _rollup(a, f, plan.by), _days(a, f)
            pf = where.filters(db, ps, pe)
            was, pdays = _rollup(a, pf, plan.by), _days(a, pf)
            if was:
                no_prior_note = ("There is no data before this period, so its second half is "
                                 "compared with its first.")
        if not was:
            dropped = [c for c in plan.where if c.op.startswith("change")]
            plan = _no_compare(plan)
            ps = pe = None
            no_prior_note = ("There is no data before this period, so nothing could be compared"
                             + (" and the condition on the change was not applied." if dropped
                                else "."))
    label = _labeller(db, plan.by, names)

    def row(key, meas, pmeas) -> dict:
        v = compute(meas, days)
        disp, ent = label(key)
        r: dict = {"label": disp, "ent": ent}
        pv = compute(pmeas, pdays) if pmeas is not None else None
        for m in plan.metrics:
            r[m] = v[m]
            if plan.compare:
                now_v, was_v = v[m], (pv[m] if pv else None)
                r[f"{m}__prior"] = was_v
                r[f"{m}__change"] = now_v - was_v if now_v is not None and was_v is not None \
                    else None
        return r

    rows = [row(k, meas, was.get(k)) for k, meas in now.items()]
    matched = [r for r in rows if passes(r, plan.where)]
    rows = _sort(matched, plan)[: plan.limit]
    total = None
    if plan.by != "total":
        tot_now = _rollup(a, f, "total").get(None)
        tot_was = _rollup(a, where.filters(db, ps, pe), "total").get(None) \
            if plan.compare and ps and pe else None
        if tot_now:
            total = row(None, tot_now, tot_was)
            total["label"], total["ent"] = "All selected", None
    cols = [Column("label", plan.by.title() if plan.by != "total" else "", "text")] \
        + metric_columns(plan)
    what = ", ".join(METRICS[m].label for m in plan.metrics)
    scope_words = where.describe()
    desc = (what
            + (f" by {plan.by}" if plan.by != "total" else "")
            + (f", {scope_words}" if scope_words else "")
            + f", {_span(start, end)}"
            + (f" against {_span(ps, pe)}" if plan.compare and ps and pe else ""))
    notes = [no_prior_note] if no_prior_note else []
    if plan.where:
        notes.append(f"{len(matched)} of {len(now)} {plan.by if plan.by != 'total' else 'rows'} "
                     f"met the condition.")
    if len(matched) > plan.limit:
        notes.append(f"Showing the first {plan.limit}.")
    return Result(title=plan.title or desc, description=desc, columns=cols, rows=rows,
                  period=_period(start, end, ps, pe), chart=chart_for(plan, cols, rows),
                  notes=notes, total=total, facts=change_facts(plan, rows))


def _no_compare(plan: Plan) -> Plan:
    from dataclasses import replace
    return replace(plan, compare=False, where=tuple(c for c in plan.where
                                                    if not c.op.startswith("change")),
                   sort=None if plan.sort and plan.sort.startswith("change:") else plan.sort)


def trend(db: Session, a: AnalyticsRepo, plan: Plan, where: Where, start: date, end: date
          ) -> Result:
    from dataclasses import replace
    plan = replace(plan, compare=False)
    f = where.filters(db, start, end)
    per_day = _rollup(a, f, "date")
    rows = []
    for d in sorted(per_day):
        v = compute(per_day[d], 1)
        rows.append({"label": d.isoformat(), "ent": None, **{m: v[m] for m in plan.metrics}})
    cols = [Column("label", "Date", "date")] + metric_columns(plan)
    scope_words = where.describe()
    desc = (", ".join(METRICS[m].label for m in plan.metrics) + " by day"
            + (f", {scope_words}" if scope_words else "") + f", {_span(start, end)}")
    return Result(title=plan.title or desc, description=desc, columns=cols, rows=rows,
                  period=_period(start, end), chart=chart_for(plan, cols, rows))


def why(db: Session, a: AnalyticsRepo, plan: Plan, where: Where, start: date, end: date,
        names: dict[str, str]) -> Result:
    f = where.filters(db, start, end)
    by = plan.by if plan.by in ("branch", "product", "division", "district", "category") else "product"
    vb = a.variance_bridge(f, by=by)  # type: ignore[arg-type]  # checked just above
    if not vb.get("available"):
        raise ToolRefused("not_enough_data", vb.get("reason") or "not enough data to compare")
    back = _reverse_labels(db, by, names)
    rows = []
    for s in vb["segments"]:
        disp, ent = back(s["label"])
        rows.append({"label": disp, "ent": ent, "current": s["current_profit"],
                     "prior": s["prior_profit"], "change": s["change"],
                     "volume": s["volume_effect"], "rate": s["rate_effect"]})
    rows = sorted(rows, key=lambda r: abs(r["change"]), reverse=True)[: plan.limit]
    cp, pp = vb["current_period"], vb["prior_period"]
    cols = [Column("label", by.title(), "text"), Column("current", "FTP profit", "bdt"),
            Column("prior", "Before", "bdt"), Column("change", "Change", "bdt"),
            Column("volume", "From balances (volume)", "bdt"),
            Column("rate", "From spreads (rate)", "bdt")]
    total = {"label": "All selected", "ent": None, "current": vb["closing_profit"],
             "prior": vb["opening_profit"], "change": vb["total_change"],
             "volume": vb["volume_effect"], "rate": vb["rate_effect"]}
    scope_words = where.describe()
    desc = (f"Change in net FTP profit by {by}, split into volume and rate"
            + (f", {scope_words}" if scope_words else "")
            + f", {_span(cp['start'], cp['end'])} against {_span(pp['start'], pp['end'])}")
    notes = []
    if vb.get("comparison_mode") == "split_window":
        notes.append("No data before this period, so its first half is compared with its second.")
    notes.append("Interaction effect (balances and spreads moving together): "
                 f"{vb['interaction_effect']} taka; the parts sum exactly to the change.")
    return Result(title=plan.title or "What moved FTP profit", description=desc, columns=cols,
                  rows=rows, period=_period(cp["start"], cp["end"], pp["start"], pp["end"]),
                  chart=chart_for(plan, cols, rows), notes=notes, total=total)


def _reverse_labels(db: Session, by: str, names: dict[str, str]):
    if by == "branch":
        def br(label: str):
            code = label.split(" ", 1)[0]
            return names.get(f"BR:{code}", label), f"{{BR:{code}}}"
        return br
    if by == "product":
        codes = {p.short_name: p.product_code for p in db.scalars(select(Product))}
        return lambda label: (label, f"{{PRD:{codes[label]}}}" if label in codes else None)
    if by == "division":
        ids = {v.removesuffix(" division"): k.split(":", 1)[1] for k, v in names.items()
               if k.startswith("DIV:")}
        return lambda label: (names.get(f"DIV:{ids.get(label)}", label),
                              f"{{DIV:{ids[label]}}}" if label in ids else None)
    if by == "district":
        codes = {d.name: d.code for d in db.scalars(select(District))}
        return lambda label: (label, f"{{DIST:{codes[label]}}}" if label in codes else None)
    return lambda label: (label, None)


def market_tool(db: Session, plan: Plan) -> Result:
    today = date.today()
    pts = sheet.market_points(db, today)
    codes = list(plan.codes) or ["BB_POLICY", "BB_CALL_ON", "BB_TBILL_91", "BB_TBILL_364",
                                 "FX_USDBDT", "US_FEDFUNDS", "US_UST_10Y"]
    if len(codes) == 1:
        s = market_catalog.BY_CODE[codes[0]]
        hist = market.series_history(db, s.code, 120)
        unit = "pct" if s.unit == "pct" else "num"
        rows = [{"label": o.obs_date.isoformat(), "ent": None, "value": o.value} for o in hist]
        cols = [Column("label", "Date", "date"), Column("value", s.short, unit)]
        chart = {"type": "line", "x": "label", "series": [cols[1].__dict__]} if len(rows) > 1 else None
        return Result(title=plan.title or s.name, description=f"{s.name}, recorded values",
                      columns=cols, rows=rows, chart=chart,
                      notes=[] if rows else ["No values recorded yet."])
    rows = []
    for c in codes:
        p = pts.get(c)
        if p is None:
            continue
        pct = p.unit == "pct"
        rows.append({"label": p.short, "ent": None, "value": p.value,
                     "change": (p.value - p.prev) if p.value is not None and p.prev is not None else None,
                     "since": p.prev_as_of.isoformat() if p.prev_as_of else None,
                     "as_of": p.as_of.isoformat() if p.as_of else None,
                     "unit": "pct" if pct else "num"})
    cols = [Column("label", "Series", "text"), Column("value", "Latest", "mixed"),
            Column("change", "Change", "mixed_change"), Column("since", "Since", "date"),
            Column("as_of", "As of", "date")]
    return Result(title=plan.title or "Market rates", description="Latest market values",
                  columns=cols, rows=rows, chart=None)


def benchmarks_tool(db: Session, plan: Plan) -> Result:
    curve = market.taka_curve(market.latest_observations(db))
    items = market.benchmarks(db, curve, include_balances=True)["items"]
    rows = [{"label": i["name"], "ent": f"{{PRD:{i['product_code']}}}",
             "benchmark": i["benchmark"], "market": i["market"],
             "gap": Decimal(i["gap_bp"]) / 100 if i["gap_bp"] is not None else None,
             "balance": i["balance"], "monthly": i["monthly_impact"],
             "basis": "behavioural" if i["behavioural"] else None} for i in items]
    # Market-priced products first, by the size of their gap: those are the
    # benchmarks ALCO can act on. Non-maturity deposits follow -- their gap is a
    # deliberate behavioural pricing policy and would otherwise always lead.
    rows.sort(key=lambda r: (r["basis"] == "behavioural", -abs(r["gap"] or 0)))
    for r in rows:
        if r["basis"] == "behavioural":
            r["basis"] = "behavioural: priced by policy, not against the market"
    rows = rows[: plan.limit]
    cols = [Column("label", "Product", "text"), Column("benchmark", "FTP benchmark", "pct"),
            Column("market", "Market at its term", "pct"), Column("gap", "Gap", "pp"),
            Column("balance", "Balance", "bdt"), Column("monthly", "Effect a month", "bdt"),
            Column("basis", "Note", "text")]
    chart = {"type": "bar", "x": "label", "horizontal": True, "series": [cols[3].__dict__]} \
        if plan.chart != "none" else None
    return Result(title=plan.title or "Benchmarks against the market",
                  description="Each product's FTP benchmark against the taka curve at its term",
                  columns=cols, rows=rows, chart=chart,
                  notes=["Products priced against the market come first, largest gap first. "
                         "Non-maturity deposits (current and savings) follow: they use a one-year "
                         "behavioural term and their gap is a pricing policy, not an error."])


def insights_tool(db: Session, reader: engine.Reader, names: dict[str, str]) -> Result:
    from app.ai.insights.facts import fill_names
    rows = [{"label": fill_names(i.title, names), "ent": i.title, "severity": i.severity,
             "money": i.money_at_stake, "basis": i.money_basis}
            for i, _ in engine.feed(db, reader, limit=25)]
    cols = [Column("label", "Finding", "text"), Column("severity", "Level", "text"),
            Column("money", "At stake", "bdt"), Column("basis", "", "text")]
    return Result(title="Open findings", description="The platform's open findings for you",
                  columns=cols, rows=rows, chart=None)


def execute(db: Session, scope: ScopeFilter, plan: Plan, where: Where, *, head_office: bool,
            reader: engine.Reader, names: dict[str, str]) -> Result:
    if plan.tool == "market":
        return market_tool(db, plan)
    if plan.tool == "benchmarks":
        if not head_office:
            raise ToolRefused("head_office", "Benchmarks against the market are bank-wide "
                                             "figures, shown to head office only.")
        return benchmarks_tool(db, plan)
    if plan.tool == "insights":
        return insights_tool(db, reader, names)
    a = AnalyticsRepo(db, scope)
    latest = a.dash.latest_business_date()
    if latest is None:
        raise ToolRefused("no_data", "No bank data has been loaded for your part of the bank yet.")
    q = select(func.min(AggDailyBranch.business_date))
    if not scope.unrestricted:
        q = q.where(AggDailyBranch.branch_id.in_(scope.branch_ids or [-1]))
    earliest = db.scalar(q) or latest
    start, end = window(plan, latest, earliest)
    if start > end:
        raise ToolRefused("no_data", f"There is no data for that period; data runs "
                                     f"{earliest:%d %b} to {latest:%d %b %Y}.")
    if plan.tool == "trend":
        if (end - start) < timedelta(days=1):
            start = max(earliest, end - timedelta(days=29))
        return trend(db, a, plan, where, start, end)
    if plan.tool == "why":
        return why(db, a, plan, where, start, end, names)
    return compare(db, a, plan, where, start, end, names)

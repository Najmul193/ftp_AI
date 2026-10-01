"""A query's result: the table a person sees, the chart, and the masked text
a provider may read.

Rows hold exact figures and real names, for the person who asked (who is
entitled to them: the query ran in their scope). A row's entity is kept as a
placeholder (`{BR:0101}`) beside its display label, so the provider's copy can
carry a token instead, with amounts blurred -- the same rules as the brief.

The chart is chosen by code from the result's own columns. A model may ask
for "bar" or "line"; it cannot put a number on the chart.

PURE: no I/O.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable

from app.ai.copilot.catalog import METRICS, Plan
from app.ai.gateway.generalize import crore, rate
from app.ai.insights.detectors import taka
from app.ai.insights.facts import fill

#: Decimal division of balances yields long fractions; rates show at 4 places.
_Q4 = Decimal("0.0001")


@dataclass(frozen=True)
class Column:
    key: str
    label: str
    #: text | bdt | pct | pp (a change in percentage points) | date | count
    unit: str


@dataclass
class Result:
    title: str
    #: What was run, in words, with placeholders for entities.
    description: str
    columns: list[Column]
    rows: list[dict]
    period: dict | None = None
    chart: dict | None = None
    notes: list[str] = field(default_factory=list)
    #: A one-line summary for the whole selection (compare by a dimension).
    total: dict | None = None
    #: What the figures show, in words written by code: shown above whatever a
    #: model writes, so a reader sees the true direction first.
    facts: list[str] = field(default_factory=list)
    #: Where the full view of this answer lives: {"label", "href"}.
    link: dict | None = None

    def to_json(self, names: dict[str, str]) -> dict:
        def clean(r: dict) -> dict:
            return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in r.items()}
        return {
            "title": self.title, "description": fill_names(self.description, names),
            "columns": [c.__dict__ for c in self.columns],
            "rows": [clean(r) for r in self.rows],
            "total": clean(self.total) if self.total else None,
            "period": {k: (v.isoformat() if hasattr(v, "isoformat") else v)
                       for k, v in (self.period or {}).items()},
            "chart": self.chart, "notes": [fill_names(n, names) for n in self.notes],
            "facts": self.facts, "link": self.link,
        }


def fill_names(text: str, names: dict[str, str]) -> str:
    return fill(text, lambda k, key: names.get(f"{k}:{key}", key))


# --- computing the metrics ----------------------------------------------------- #

def _d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else Decimal(0)


def compute(meas: dict, days: int) -> dict[str, Decimal | None]:
    """Every catalogue metric from one rollup's summed measures.

    Annualised exactly as `AnalyticsRepo.banking_ratios`: an amount over the
    balance-days that earned it, each day over its own divisor.
    """
    rec, pay = _d(meas.get("interest_receivable")), _d(meas.get("interest_payable"))
    a_pd, l_pd, t_pd = (_d(meas.get("asset_balance_pd")), _d(meas.get("liability_balance_pd")),
                        _d(meas.get("total_balance_pd")))

    def ann(x: Decimal, base: Decimal) -> Decimal | None:
        return (x / base).quantize(_Q4) if base else None

    yoa, cod = ann(rec, a_pd), ann(pay, l_pd)
    return {
        "net_ftp_profit": _d(meas.get("net_ftp_profit")),
        "lending_ftp_profit": _d(meas.get("asset_ftp_profit")),
        "deposit_ftp_profit": _d(meas.get("liability_ftp_profit")),
        "deposits": (_d(meas.get("liability_balance")) / days).quantize(Decimal("0.01")) if days else None,
        "advances": (_d(meas.get("asset_balance")) / days).quantize(Decimal("0.01")) if days else None,
        "cost_of_deposits": cod,
        "yield_on_advances": yoa,
        "nim": ann(rec - pay, a_pd),
        "spread": (yoa - cod) if yoa is not None and cod is not None else None,
        "ftp_yield": ann(_d(meas.get("net_ftp_profit")), t_pd),
        # Counts summed over days, so a day's average is the count a reader means.
        "accounts": (_d(meas.get("account_count")) / days).quantize(Decimal(1)) if days else None,
        "loss_making_accounts": (_d(meas.get("negative_ftp_count")) / days).quantize(Decimal(1))
        if days else None,
    }


def passes(row: dict, conditions) -> bool:
    for c in conditions:
        now = row.get(c.metric)
        ch = row.get(f"{c.metric}__change")
        v = ch if c.op.startswith("change") else now
        if v is None:
            return False
        if c.op.endswith("gt") and not v > c.value:
            return False
        if c.op.endswith("lt") and not v < c.value:
            return False
    return True


def change_facts(plan: Plan, rows: list[dict], max_rows: int = 6) -> list[str]:
    """One plain sentence per row and measure: which way it moved, by how much.

    Only for a comparison of a few rows -- a named branch or two, a handful of
    products -- where the direction is the answer. Written by code, so it is
    right even when a model's narration is not.
    """
    if not plan.compare or not rows or len(rows) > max_rows:
        return []
    out = []
    for r in rows:
        parts = []
        for m in plan.metrics:
            now, was, ch = r.get(m), r.get(f"{m}__prior"), r.get(f"{m}__change")
            if now is None or was is None or ch is None:
                continue
            meta = METRICS[m]
            if meta.unit == "pct":
                b = int((Decimal(str(ch)) * 100).to_integral_value())
                move = "unchanged" if b == 0 else f"{'up' if b > 0 else 'down'} {abs(b)} bp"
                parts.append(f"{meta.label.lower()} {move} ({Decimal(str(was)):.2f}% → "
                             f"{Decimal(str(now)):.2f}%)")
            else:
                c = Decimal(str(ch))
                move = "unchanged" if c == 0 else f"{'up' if c > 0 else 'down'} {taka(abs(c))}"
                parts.append(f"{meta.label.lower()} {move} ({taka(was)} → {taka(now)})")
        if parts:
            out.append(f"{r.get('label')}: " + "; ".join(parts))
    return out


def metric_columns(plan: Plan) -> list[Column]:
    cols = []
    for m in plan.metrics:
        meta = METRICS[m]
        cols.append(Column(m, meta.label, meta.unit))
        if plan.compare:
            cols.append(Column(f"{m}__prior", f"{meta.label}, before", meta.unit))
            cols.append(Column(f"{m}__change", "Change", "pp" if meta.unit == "pct" else meta.unit))
    return cols


# --- the chart ----------------------------------------------------------------- #

def chart_for(plan: Plan, columns: list[Column], rows: list[dict]) -> dict | None:
    """One axis, one unit: series that do not share the first one's unit are
    left to the table rather than put on a second scale."""
    if plan.chart == "none" or not rows:
        return None
    numeric = [c for c in columns if c.unit in ("bdt", "pct", "pp", "count")
               and not c.key.endswith("__prior")]
    if not numeric:
        return None
    lead = numeric[0]
    if plan.compare and plan.sort and plan.sort.startswith("change:"):
        lead = next((c for c in numeric if c.key == f"{plan.sort[7:]}__change"), lead)
    if plan.tool == "trend":
        series = [c for c in numeric if c.unit == lead.unit and not c.key.endswith("__change")][:4]
        return {"type": "line", "x": "label", "series": [c.__dict__ for c in series]}
    if len(rows) < 2 and plan.tool != "why":
        return None
    if plan.tool == "why":
        keys = ["volume", "rate"]
        series = [c for c in columns if c.key in keys]
        return {"type": "bar", "x": "label", "stack": True, "horizontal": True,
                "series": [c.__dict__ for c in series]}
    return {"type": "bar", "x": "label", "horizontal": len(rows) > 6,
            "series": [lead.__dict__]}


# --- what the provider reads ------------------------------------------------------ #

def _val(v, unit: str) -> str:
    if v is None or v == "":
        return "n/a"
    if unit == "bdt":
        return crore(Decimal(str(v)))
    if unit == "pct":
        return rate(Decimal(str(v)))
    if unit == "pp":
        b = int((Decimal(str(v)) * 100).to_integral_value())
        return f"{'+' if b > 0 else ''}{b} bp"
    if unit == "num":
        return f"{Decimal(str(v)):.2f}"
    if unit == "count":
        return f"{int(Decimal(str(v))):,}"
    return str(v)


def masked_text(res: Result, resolve: Callable[[str, str], str], *, max_rows: int = 25) -> str:
    """The result as a provider may see it: tokens, crore to 3 s.f., rates to 2 dp.

    Each row is one line with its before, after and change together, so the
    grounding check accepts differences a narrator draws within a row only.
    """
    out = [f"RAN: {fill(res.description, resolve)}"]
    if res.period:
        p = res.period
        line = f"PERIOD: {p['start']} to {p['end']}"
        if p.get("prior_start"):
            line += f", compared with {p['prior_start']} to {p['prior_end']}"
        out.append(line)
    out.append("Amounts in BDT, cr (crore) or lakh as marked, 3 significant figures; rates in % a year.")
    cols = [c for c in res.columns if c.key != "label"]

    def row_text(r: dict) -> str:
        label = fill(r["ent"], resolve) if r.get("ent") else r.get("label", "")
        def unit(c: Column) -> str:
            # Market rows mix rates and prices: each row says which it is.
            if c.unit == "mixed":
                return r.get("unit", "num")
            if c.unit == "mixed_change":
                # An amount's change is an amount: it leaves as crore, never exact.
                return "pp" if r.get("unit") == "pct" else "bdt" if r.get("unit") == "bdt" else "num"
            return c.unit
        parts = [f"{c.label} {_val(r.get(c.key), unit(c))}" for c in cols if c.unit != "text"]
        texts = [f"{c.label} {r.get(c.key)}" for c in cols if c.unit == "text" and r.get(c.key)]
        return f"- {label}: " + "; ".join(parts + texts)

    if res.total:
        # Said outright: a model read a two-branch total as one branch's
        # earlier value.
        # Said outright: a model read a two-branch total as one branch's earlier
        # value, and a division total as the sum of the five rows listed.
        out.append("TOTAL FOR EVERYTHING SELECTED (all matching rows, not only those listed "
                   "below; a total, not an earlier value): " + row_text(res.total)[2:])
    out.append(f"ROWS ({len(res.rows)}{', first ' + str(max_rows) if len(res.rows) > max_rows else ''}):")
    out += [row_text(r) for r in res.rows[:max_rows]]
    out += [f"NOTE: {fill(n, resolve)}" for n in res.notes]
    return "\n".join(out)


# --- does the wording say the figures moved the way they did? ------------------ #

_DOWN = re.compile(r"\b(fell|fall|falls|falling|lost|lose|loses|losing|declin\w*|decreas\w*|"
                   r"drop\w*|down|shr[ai]nk\w*|lower\w*|reduc\w*|weaken\w*)\b", re.I)
_UP = re.compile(r"\b(rose|rise|rises|rising|grew|grow|grows|growing|growth|increas\w*|gain\w*|"
                 r"up|higher|improv\w*|climb\w*|strengthen\w*)\b", re.I)
_METRIC_WORDS: dict[str, re.Pattern] = {
    "deposits": re.compile(r"\bdeposits?\b(?!\s+cost)", re.I),
    "advances": re.compile(r"\b(advances|loans?|lending)\b", re.I),
    "cost_of_deposits": re.compile(r"\bcost of deposits?|deposit cost", re.I),
    "yield_on_advances": re.compile(r"\byield on (advances|loans)|loan yield", re.I),
    "net_ftp_profit": re.compile(r"\b(ftp )?profit\b", re.I),
    "nim": re.compile(r"\b(nim|net interest margin|margin)\b", re.I),
    "ftp_yield": re.compile(r"\bftp yield\b", re.I),
}
_CLAUSE = re.compile(r"(?<=[.;\n])|\bwhile\b|\bwhereas\b|\bbut\b|,", re.I)


def direction_conflicts(text: str, plan: Plan, rows: list[dict]) -> list[str]:
    """Places where the wording moves a row's measure the other way from the
    figures: "X lost deposits" when X's deposits rose.

    A clause is judged only when it names exactly one row, one measure (or
    inherits the sentence's) and one direction; anything less clear is left
    alone. Grounding checks the numbers; this checks their direction.
    """
    if not plan.compare or not rows:
        return []
    labels = [(r["label"], r) for r in rows if r.get("label")]
    out: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
        sentence_metric = [m for m in plan.metrics if m in _METRIC_WORDS
                           and _METRIC_WORDS[m].search(sentence)]
        for clause in _CLAUSE.split(sentence):
            if not clause or not clause.strip():
                continue
            named = [(lab, r) for lab, r in labels if lab.lower() in clause.lower()]
            if len(named) != 1:
                continue
            metric = [m for m in plan.metrics if m in _METRIC_WORDS
                      and _METRIC_WORDS[m].search(clause)] or sentence_metric
            down, up = bool(_DOWN.search(clause)), bool(_UP.search(clause))
            if len(metric) != 1 or down == up:
                continue
            ch = named[0][1].get(f"{metric[0]}__change")
            if ch is None or Decimal(str(ch)) == 0:
                continue
            if (Decimal(str(ch)) > 0) == down:
                what = f"{named[0][0]} {METRICS[metric[0]].label.lower()}"
                if what not in out:
                    out.append(what)
    return out

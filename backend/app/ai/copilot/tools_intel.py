"""The copilot's forward-looking tools: forecasts, the policy outlook,
scenarios and the market's posted rates.

Each returns a `Result` like the other tools, so the table, the chart, the
masked text a provider reads and the grounding check all work unchanged.
Every number comes from code (`forecast`, `scenario`, `public`); the model
only chooses which to run.
"""

from __future__ import annotations

import json
from decimal import Decimal
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.copilot.catalog import Plan
from app.ai.copilot.result import Column, Result
from app.ai.gateway.generalize import crore
from app.ai.insights.detectors import taka
from app.ai.copilot.tools import ToolRefused, Where
from app.ai.forecast import service as forecast
from app.ai.public import service as public
from app.ai.scenario import engine as scenario_engine
from app.ai.scenario import service as scenario
from app.domain.scope import ScopeFilter
from app.models import Branch, Product

_OUTLOOK = {"label": "Open the Outlook", "href": "#/outlook"}


def _signed_crore(v) -> str:
    x = Decimal(str(v))
    return ("+" if x > 0 else "-" if x < 0 else "") + crore(abs(x))


def _signed_taka(v) -> str:
    x = Decimal(str(v))
    return ("+" if x > 0 else "-" if x < 0 else "") + taka(abs(x))


def _cone(item: dict, unit: str) -> dict:
    """A chart the page draws as history plus a forecast cone."""
    return {"type": "cone", "unit": unit,
            "history": [{"date": str(p["date"]), "value": str(p["value"])} for p in item["history"][-60:]],
            "forecast": [{"date": str(p["date"]), "p10": str(p["p10"]), "p50": str(p["p50"]),
                          "p90": str(p["p90"])} for p in item["forecast"]]}


def forecast_tool(db: Session, scope: ScopeFilter, plan: Plan, where: Where) -> Result:
    label = ""
    if where.branch_codes:
        b = db.scalar(select(Branch).where(Branch.branch_code == where.branch_codes[0]))
        label = f"{{BR:{b.branch_code}}}" if b else ""
    book = forecast.book_outlook(db, forecast.BookScope(scope, where, label), plan.metrics)
    if not book.get("available"):
        raise ToolRefused("no_data", book.get("reason") or "no bank data to forecast from")
    rows = []
    first = None
    for m in book["metrics"]:
        mo = m.get("month") or {}
        unit = "bdt" if m["unit"] == "bdt" else "pct"
        flow = m["kind"] == "flow"
        rows.append({
            "label": f"{m['label']}{', the month' if flow else ' at month-end'}", "ent": None,
            "unit": unit, "today": mo.get("so_far") if flow else m["last"]["value"],
            "likely": mo.get("p50"), "low": mo.get("p10"), "high": mo.get("p90"),
            "last_month": mo.get("previous_month") if flow else None,
            "q_likely": (m.get("quarter") or {}).get("p50"),
            "confidence": m["confidence"],
        })
        first = first or m
    cols = [Column("label", "Measure", "text"),
            Column("today", "So far / today", "mixed"), Column("likely", "Likely", "mixed"),
            Column("low", "Low (P10)", "mixed"), Column("high", "High (P90)", "mixed"),
            Column("last_month", "Last month", "mixed"), Column("q_likely", "Quarter-end, likely", "mixed"),
            Column("confidence", "Confidence", "text")]
    who = f" for {label}" if label else ""
    desc = (f"Forecast{who} to month-end ({book['month_end']}) and quarter-end "
            f"({book['quarter_end']}) from {book['days_of_history']} days of data to {book['latest']}")
    notes = [f"{first['label']}: {first['confidence_reason']}."] if first else []
    notes.append("Forecasts are a damped trend on the book's own history; the range is how far "
                 "such forecasts have missed before. Flows ('the month') are the month's total.")
    return Result(title=plan.title or f"Where the book is heading{who}", description=desc,
                  columns=cols, rows=rows, chart=_cone(first, first["unit"]) if first else None,
                  notes=notes, link=_OUTLOOK)


def market_forecast_tool(db: Session, plan: Plan) -> Result:
    out = forecast.market_outlook(db)
    by = {s["code"]: s for s in out["series"]}
    rows, first = [], None
    for code in plan.codes:
        s = by.get(code)
        if s is None:
            continue
        unit = "pct" if s["unit"] == "pct" else "num"
        rows.append({"label": s["short"], "ent": None, "unit": unit, "today": s["last"]["value"],
                     "d30": (s["in_30d"] or {}).get("p50"), "d90": (s["in_90d"] or {}).get("p50"),
                     "low": (s["in_90d"] or {}).get("p10"), "high": (s["in_90d"] or {}).get("p90"),
                     "confidence": s["confidence"]})
        first = first or s
    if not rows:
        raise ToolRefused("no_data", "There is not enough history for those series yet.")
    cols = [Column("label", "Series", "text"), Column("today", "Latest", "mixed"),
            Column("d30", "In 30 days", "mixed"), Column("d90", "In 90 days", "mixed"),
            Column("low", "90-day low (P10)", "mixed"), Column("high", "90-day high (P90)", "mixed"),
            Column("confidence", "Confidence", "text")]
    return Result(title=plan.title or "Market rates, 90 days ahead",
                  description="Market series forecast 90 days ahead from their own history",
                  columns=cols, rows=rows,
                  chart=_cone(first, "pct" if first["unit"] == "pct" else "num") if first else None,
                  notes=[f"{first['short']}: {first['confidence_reason']}."] if first else [],
                  link=_OUTLOOK)


def policy_tool(db: Session, plan: Plan) -> Result:
    o = forecast.policy_outlook(db)
    rows = [{"label": d["label"], "ent": None, "reading": d["value"],
             "push": Decimal(str(d["push"])), "meaning": d["explain"]}
            for d in sorted(o["drivers"], key=lambda d: -abs(d["push"]))]
    cols = [Column("label", "Signal", "text"), Column("reading", "Reading", "text"),
            Column("push", "Push (+ hike, - cut)", "num"), Column("meaning", "What it means", "text")]
    odds = o["odds"]
    notes = [f"Leaning: {o['leaning']}. Signals split hike {odds['hike']}%, hold {odds['hold']}%, "
             f"cut {odds['cut']}% -- a summary of the signals, not a market price.",
             f"Repo now {o['repo']}%. Next meeting expected around {o['next_meeting']} "
             f"({o['next_meeting_basis']})."]
    if o["missing"]:
        notes.append(f"Not yet available: {', '.join(o['missing'])}.")
    return Result(title=plan.title or "The next policy meeting",
                  description="Bangladesh Bank's policy rate: which way the signals lean",
                  columns=cols, rows=rows, chart=None, notes=notes, link=_OUTLOOK)


def scenario_tool(db: Session, scope: ScopeFilter, plan: Plan, where: Where) -> Result:
    products = {p.product_code for p in db.scalars(select(Product))}
    sc = dict(plan.scenario or {})
    if where.branch_codes:
        sc["branches"] = list(where.branch_codes)
    try:
        s = scenario_engine.parse(sc, products=products)
    except scenario_engine.ScenarioError as exc:
        raise ToolRefused("bad_scenario", f"That scenario could not be set up: {exc}") from exc
    r = scenario.run(db, scope, s)
    if not r.get("available"):
        raise ToolRefused("no_data", r.get("reason") or "no book to simulate")
    b, n, c = r["base"], r["scenario_totals"], r["change"]
    rows = [
        {"label": "Bank NII, a month", "ent": None, "unit": "bdt", "today": b["bank_nii"],
         "after": n["bank_nii"], "change": c["bank_nii"]},
        {"label": "Branches' FTP profit, a month", "ent": None, "unit": "bdt",
         "today": b["branch_ftp"], "after": n["branch_ftp"], "change": c["branch_ftp"]},
        {"label": "Treasury's result, a month", "ent": None, "unit": "bdt", "today": b["treasury"],
         "after": n["treasury"], "change": c["treasury"]},
        {"label": "Deposits", "ent": None, "unit": "bdt", "today": b["deposits"],
         "after": n["deposits"], "change": c["deposits"]},
        {"label": "Advances", "ent": None, "unit": "bdt", "today": b["advances"],
         "after": n["advances"], "change": c["advances"]},
        {"label": "NIM, % a year", "ent": None, "unit": "pct", "today": b["nim"], "after": n["nim"],
         "change": c["nim"]},
    ]
    cols = [Column("label", "Measure", "text"), Column("today", "Today", "mixed"),
            Column("after", "Under the scenario", "mixed"), Column("change", "Change", "mixed_change")]
    settings = ", ".join(f"{k} {v}" for k, v in s.to_dict().items()
                         if v not in (0, 0.0, {}, [], None) and v != getattr(scenario_engine.Scenario(), k))
    # Amounts in notes as crore: notes reach the provider as written.
    notes = ["Where the NII change comes from, a month: " + "; ".join(
        f"{w['label'].lower()} {_signed_crore(w['value'])}" for w in r["waterfall"]) + "."]
    worst = [p for p in r["by_product"] if p["ftp_change"] < 0][:3]
    if worst:
        notes.append("Products whose FTP profit falls most, a month: " + "; ".join(
            f"{{PRD:{p['key']}}} {_signed_crore(p['ftp_change'])}" for p in worst) + ".")
    notes.append("Moving FTP benchmarks shifts profit between branches and treasury; only "
                 "customer rates, balances and the market move bank NII.")
    return Result(title=plan.title or "What if",
                  description=f"Scenario over {s.horizon_months} months: {settings or 'no change'}",
                  columns=cols, rows=rows, chart=None, notes=notes,
                  link={"label": "Open in the Scenario lab",
                        "href": "#/scenario?s=" + quote(json.dumps(s.to_dict()))},
                  facts=[f"Bank NII changes by {_signed_taka(c['bank_nii'])} a month; branches' "
                         f"FTP profit by {_signed_taka(c['branch_ftp'])}; treasury by "
                         f"{_signed_taka(c['treasury'])}."])


def peer_tool(db: Session, scope: ScopeFilter, plan: Plan) -> Result:
    items = public.book_vs_peers(db, branch_ids=None if scope.unrestricted
                                 else list(scope.branch_ids or []))
    if items:
        if plan.side:
            items = [i for i in items if i["side"] == plan.side]
        rows = [{"label": i["name"], "ent": f"{{PRD:{i['product_code']}}}", "unit": "pct",
                 "ours": i["our_rate"], "pcb": i["pcb_median"], "median": i["market_median"],
                 "gap": (Decimal(i["our_rate"]) - Decimal(i["pcb_median"] or i["market_median"])),
                 "balance": i["balance"], "compared": i["peer_label"]}
                for i in sorted(items, key=lambda i: -abs(Decimal(i["gap"])) * Decimal(i["balance"]))]
        cols = [Column("label", "Product", "text"), Column("ours", "Our customer rate", "pct"),
                Column("pcb", "Private banks' median", "pct"), Column("median", "All banks' median", "pct"),
                Column("gap", "Gap to private banks", "pp"), Column("balance", "Balance", "bdt"),
                Column("compared", "Compared with", "text")]
        month = next((i["month"] for i in items if i.get("month")), None)
        return Result(title=plan.title or "Our rates against other banks",
                      description=f"Our product rates against Bangladesh Bank's bank-wise posted rates ({month})",
                      columns=cols, rows=rows,
                      chart={"type": "bar", "x": "label", "horizontal": True,
                             "series": [cols[4].__dict__]},
                      notes=["Each product is matched to the table's nearest line by name and term."],
                      link=_OUTLOOK)
    book = "lending" if plan.side == "ASSET" else "deposit"
    t = public.peer_table(db, book)
    rows = [{"label": p["label"], "ent": None, "median": p["median"], "pcb": p["pcb_median"],
             "low": p["p25"], "high": p["p75"], "banks": p["banks"]} for p in t["products"]]
    cols = [Column("label", "Product", "text"), Column("median", "All banks' median", "pct"),
            Column("pcb", "Private banks' median", "pct"), Column("low", "Lower quartile", "pct"),
            Column("high", "Upper quartile", "pct"), Column("banks", "Banks", "count")]
    return Result(title=plan.title or f"What banks post for {book}",
                  description=f"Bangladesh Bank's bank-wise {book} rates, {t['month']}",
                  columns=cols, rows=rows, chart=None,
                  notes=["No products of yours to compare with these yet."])


def execute(db: Session, scope: ScopeFilter, plan: Plan, where: Where, *, head_office: bool,
            can_scenario: bool) -> Result:
    if plan.tool == "forecast":
        return forecast_tool(db, scope, plan, where)
    if plan.tool == "market_forecast":
        return market_forecast_tool(db, plan)
    if plan.tool == "policy_outlook":
        return policy_tool(db, plan)
    if plan.tool == "scenario":
        if not can_scenario:
            raise ToolRefused("no_permission", "Running scenarios needs the scenario permission "
                                               "(analysts and treasury have it).")
        return scenario_tool(db, scope, plan, where)
    if plan.tool == "peer_compare":
        return peer_tool(db, scope, plan)
    raise ToolRefused("unknown", f"unknown tool {plan.tool}")


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


def _resolve_categories(db: Session, words: tuple[str, ...]) -> list[tuple[str, str]]:
    """Rate types in words -> (book, category), through the explorer's search
    (which also knows our own product names)."""
    from app.ai.public import explorer
    from app.ai.public import parse as pp
    out: list[tuple[str, str]] = []
    for w in words:
        if w in pp.PRODUCT_LABELS:
            out.append(("deposit" if w in pp.DEPOSIT_PRODUCTS else "lending", w))
            continue
        hit = next((h for h in explorer.search(db, w) if h["type"] in ("category", "product")
                    and h.get("product")), None)
        if hit is None:
            raise ToolRefused("unknown_rate", f"No rate type in Bangladesh Bank's tables matches "
                                              f"{w!r}. Try e.g. '1 year FD', 'savings', 'home loan'.")
        out.append((hit["book"], hit["product"]))
    return list(dict.fromkeys(out))


def market_rates_tool(db: Session, scope: ScopeFilter, plan: Plan) -> Result:
    """Other banks' posted rates, by bank and/or rate type, with ours beside."""
    from app.ai.public import banks as bank_dir
    from app.ai.public import explorer
    d = public.directory(db)
    chosen: list[bank_dir.Bank] = []
    for w in plan.peer_banks:
        b = bank_dir.resolve_bank(w, d)
        if b is None:
            raise ToolRefused("unknown_bank", f"No bank in Bangladesh Bank's tables matches {w!r}.")
        chosen.append(b)
    cats = _resolve_categories(db, plan.categories)
    book = cats[0][0] if cats else ("lending" if plan.side == "ASSET" else "deposit")
    cats = [c for c in cats if c[0] == book] or cats
    order = plan.order if plan.order in ("asc", "desc") else ("desc" if book == "deposit" else "asc")
    branch_ids = None if scope.unrestricted else list(scope.branch_ids or [])
    grid = explorer.grid(db, book, plan.peer_set or ("all" if chosen else "competitors"), branch_ids,
                         plan.basis)
    if not grid["categories"]:
        raise ToolRefused("no_data", "Bangladesh Bank's bank-wise tables have not been collected yet.")
    self_bank = grid["self_bank"]
    by_code = {r["code"]: r for r in grid["banks"]}
    meta_cat = {c["product"]: c for c in grid["categories"]}
    month = grid["month"]
    rows: list[dict] = []
    if chosen and not cats:
        # Each named bank's whole card, category by category, against ours.
        cols = [Column("label", "Rate type", "text")]
        for b in chosen:
            cols.append(Column(f"b_{b.code}", bank_dir.bank_of(b.code, d).name, "pct"))
        cols += [Column("self", "Our posted rate", "pct"), Column("median", "All banks' median", "pct")]
        if plan.include_ours:
            cols.append(Column("book", "Our customers get", "pct"))
        for c in grid["categories"]:
            r = {"label": c["label"], "ent": None, "self": c["self"], "median": c["median"],
                 "book": (c["book"] or {}).get("rate") if plan.include_ours else None}
            for b in chosen:
                r[f"b_{b.code}"] = (by_code.get(b.code) or {}).get("rates", {}).get(c["product"])
            rows.append(r)
        title = plan.title or f"{', '.join(b.name for b in chosen)} against us"
        desc = f"Posted {book} rates by type, {month:%B %Y}"
        chart = None
    else:
        cats = cats or [(book, grid["categories"][0]["product"])]
        if chosen:
            codes = [b.code for b in chosen]
        else:
            in_set = [r for r in grid["banks"] if r["in_set"] and not r["self"]]
            key = cats[0][1]
            ranked = sorted((r for r in in_set if r["rates"].get(key) is not None),
                            key=lambda r: r["rates"][key], reverse=order == "desc")
            codes = [r["code"] for r in ranked[: plan.limit]]
        if self_bank not in codes:
            codes.append(self_bank)
        cols = [Column("label", "Bank", "text")] + [
            Column(f"c_{p}", meta_cat[p]["label"] if p in meta_cat else p, "pct") for _, p in cats]
        for code in codes:
            r = {"label": bank_dir.bank_of(code, d).name + (" (us, posted)" if code == self_bank else ""),
                 "ent": None}
            for _, p in cats:
                r[f"c_{p}"] = (by_code.get(code) or {}).get("rates", {}).get(p)
            rows.append(r)
        rows.append({"label": "All banks' median", "ent": None,
                     **{f"c_{p}": (meta_cat.get(p) or {}).get("median") for _, p in cats}})
        if plan.include_ours:
            rows.append({"label": "Our customers actually get", "ent": None,
                         **{f"c_{p}": ((meta_cat.get(p) or {}).get("book") or {}).get("rate")
                            for _, p in cats}})
        title = plan.title or ", ".join(meta_cat[p]["label"] for _, p in cats if p in meta_cat)
        desc = (f"Posted {book} rates, {month:%B %Y}"
                + ("" if chosen else f", {grid['peer_label']}, "
                   f"{'highest' if order == 'desc' else 'lowest'} first"))
        chart = {"type": "bar", "x": "label", "horizontal": True, "series": [cols[1].__dict__]}
    ranks = [f"{c['label']}: we rank {c['rank']} of {c['banks']}" for c in grid["categories"]
             if c["rank"] and (not cats or c["product"] in {p for _, p in cats})][:4]
    notes = ([("Each bank's best posted offer (its highest deposit rate, lowest loan rate)"
               if plan.basis == "best" else "The middle of each bank's posted range")
              + ", as Bangladesh Bank publishes them. 'Our customers actually get' is our book's "
                "real average rate."]
             + ([f"Our rank ({'highest-paying' if book == 'deposit' else 'cheapest'} first): "
                 + "; ".join(ranks) + "."] if ranks else []))
    return Result(title=title, description=desc, columns=cols, rows=rows, chart=chart, notes=notes,
                  link={"label": "Open Market rates",
                        "href": f"#/market?book={book}"
                                + (f"&product={cats[0][1]}" if cats else "")
                                + (f"&bank={quote(chosen[0].code)}" if chosen else "")})


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
    if plan.tool == "market_rates":
        return market_rates_tool(db, scope, plan)
    raise ToolRefused("unknown", f"unknown tool {plan.tool}")


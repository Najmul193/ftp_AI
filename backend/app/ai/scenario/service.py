"""Scenarios from the database: the book's lines, live presets, saved runs, and
a scenario read out of a sentence.

Lines are read from the branch x product aggregate over the last week of
data, in the asker's scope: a division user's scenario moves their division.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.forecast import service as forecast
from app.ai.gateway import gateway as gw
from app.ai.gateway.gateway import Caller, GatewayRequest, Segment, Turn
from app.ai.market.tenor import infer_tenor_days
from app.ai.models import SavedScenario
from app.ai.public import service as public
from app.ai.scenario import engine
from app.domain.scope import ScopeFilter
from app.models import AggDailyBranchProduct, Branch, Product

BASE_DAYS = 7
MAX_SAVED = 30


# --- the book's lines ----------------------------------------------------------- #

def lines(db: Session, scope: ScopeFilter) -> tuple[list[engine.Line], dict]:
    m = AggDailyBranchProduct
    q = select(func.max(m.business_date))
    if not scope.unrestricted:
        q = q.where(m.branch_id.in_(scope.branch_ids or [-1]))
    latest = db.scalar(q)
    if latest is None:
        return [], {"latest": None}
    start = latest - timedelta(days=BASE_DAYS - 1)
    stmt = (select(m.branch_code, m.product_code, m.side,
                   func.sum(m.total_balance), func.sum(m.roi_contrib),
                   func.sum(m.benchmark_contrib), func.sum(m.liquidity_contrib),
                   func.sum(m.other_contrib), func.avg(m.day_divisor))
            .where(m.business_date >= start, m.business_date <= latest)
            .group_by(m.branch_code, m.product_code, m.side))
    if not scope.unrestricted:
        stmt = stmt.where(m.branch_id.in_(scope.branch_ids or [-1]))
    prods = {p.product_code: p for p in db.scalars(select(Product))}
    # An average day of the window: a line absent on some days (an account
    # opened mid-week) counts those days as zero, as the totals do.
    dq = select(func.count(func.distinct(m.business_date))).where(
        m.business_date >= start, m.business_date <= latest)
    if not scope.unrestricted:
        dq = dq.where(m.branch_id.in_(scope.branch_ids or [-1]))
    days = db.scalar(dq) or 1
    out = []
    for br, code, side, bal, roi_c, b_c, l_c, o_c, div in db.execute(stmt):
        bal = Decimal(bal or 0)
        if not bal:
            continue
        side = getattr(side, "value", side)
        sgn = Decimal(1) if side == "ASSET" else Decimal(-1)
        p = prods.get(code)
        nature = p.liability_nature.value if p is not None and p.liability_nature else None
        tenor, _ = infer_tenor_days(p.short_name if p else code, side, nature)
        # The signed contributions back to rates: see AggDailyBranchProduct.
        out.append(engine.Line(
            branch=br, product=code, side=side, nature=nature, tenor_days=tenor,
            balance=bal / days, roi=sgn * Decimal(roi_c or 0) / bal,
            benchmark=-sgn * Decimal(b_c or 0) / bal,
            liquidity=-Decimal(l_c or 0) / bal, other=-Decimal(o_c or 0) / bal,
            divisor=Decimal(div or 36500)))
    return out, {"latest": latest, "start": start, "days": BASE_DAYS}


def market_rate(db: Session) -> tuple[Decimal, str]:
    """What surplus funds earn today: the 91-day T-bill, else call money."""
    for code, label in (("BB_TBILL_91", "91-day T-bill"), ("BB_CALL_ON", "call money")):
        h = forecast._history(db, code, 60)
        if h:
            return Decimal(h[-1][1]), label
    return Decimal("8.0"), "an assumed 8%"


def run(db: Session, scope: ScopeFilter, s: engine.Scenario) -> dict:
    ls, meta = lines(db, scope)
    if not ls:
        return {"available": False, "reason": "no bank data in your part of the bank yet"}
    rate, rate_label = market_rate(db)
    out = engine.run(ls, s, market_rate=rate)
    names = {p.product_code: p.short_name for p in db.scalars(select(Product))}
    bnames = {b.branch_code: b.branch_name for b in db.scalars(select(Branch))}
    for r in out["by_product"]:
        r["label"] = names.get(r["key"], r["key"])
    for r in out["by_branch"]:
        r["label"] = f"{bnames.get(r['key'], r['key'])} ({r['key']})"
    out["by_branch"] = out["by_branch"][:10] + [r for r in out["by_branch"][-5:]
                                                if r not in out["by_branch"][:10]]
    out["path"] = engine.path(ls, s, market_rate=rate)
    out.update(available=True, base_window=meta, market_rate_label=rate_label,
               lines=len(ls))
    return out


# --- presets, built from today's market ------------------------------------------- #

def presets(db: Session, *, head_office: bool) -> list[dict]:
    S = engine.Scenario
    out = [
        {"key": "bb_hike_50", "label": "Bangladesh Bank raises 50 bp",
         "why": "The policy rate up half a point; the curve follows.",
         "scenario": S(market_bp=50).to_dict()},
        {"key": "bb_cut_50", "label": "Bangladesh Bank cuts 50 bp",
         "why": "The policy rate down half a point; the curve follows.",
         "scenario": S(market_bp=-50).to_dict()},
        {"key": "tbill_fall_100", "label": "T-bill yields fall 100 bp",
         "why": "Government paper rallies; we pass a third of it on.",
         "scenario": S(market_bp=-100, deposit_pass=0.33, loan_pass=0.33).to_dict()},
        {"key": "liquidity_squeeze", "label": "Liquidity squeeze: +150 bp",
         "why": "Call money and short rates jump; deposits reprice slowly, competitors chase "
                "deposits.",
         "scenario": S(market_bp=150, deposit_pass=0.3, demand_pass=0.1, loan_pass=0.5,
                       competitor_bp=50, horizon_months=1).to_dict()},
        {"key": "deposit_flight", "label": "Deposits fall 5%",
         "why": "A loss of confidence or a competitor's campaign.",
         "scenario": S(deposit_growth_pct=-5).to_dict()},
    ]
    # From the policy outlook: what the market expects, played out.
    try:
        mk = {m["code"]: m for m in forecast.market_outlook(db)["series"]}
        call = mk.get("BB_CALL_ON")
        if call and call.get("in_90d"):
            move = int(round((float(call["in_90d"]["p50"]) - float(call["last"]["value"])) * 100))
            out.insert(0, {"key": "outlook", "label": f"The outlook plays out ({move:+d} bp)",
                           "why": "Short rates move as the 90-day forecast for call money expects.",
                           "scenario": S(market_bp=move).to_dict()})
    except Exception:  # noqa: BLE001 - a preset is a convenience, never a failure
        pass
    if head_office:
        gaps = [g for g in public.book_vs_peers(db)
                if g["side"] == "LIABILITY" and g["pcb_median"] is not None
                and g["peer_product"].startswith("fd_")]
        under = [g for g in gaps if g["our_rate"] < g["pcb_median"]]
        if under:
            avg = sum((g["pcb_median"] - g["our_rate"]) for g in under) / len(under)
            out.append({"key": "competitors_lift", "label": "Other banks raise term-deposit rates 50 bp",
                        "why": f"Private banks already pay about {int(avg * 100)} bp more than us on "
                               f"term deposits ({under[0]['month']:%B %Y}); if they go further, "
                               f"depositors compare.",
                        "scenario": S(competitor_bp=50, horizon_months=6).to_dict()})
            out.append({"key": "match_pcb", "label": "Match the private banks on term deposits",
                        "why": "Raise each term deposit below the private banks' median to it: "
                               "the cost, against the deposits it brings in.",
                        "scenario": S(horizon_months=6,
                                      product_rate_bp={g["product_code"]: int(round(
                                          (g["pcb_median"] - g["our_rate"]) * 100))
                                          for g in under}).to_dict()})
    return out


# --- saved scenarios ----------------------------------------------------------------- #

def save(db: Session, user_id: int, name: str, s: engine.Scenario, summary: dict) -> SavedScenario:
    n = db.scalar(select(func.count()).select_from(SavedScenario)
                  .where(SavedScenario.user_id == user_id)) or 0
    if n >= MAX_SAVED:
        # Bounded: the oldest goes, so the list never grows without end.
        oldest = db.scalar(select(SavedScenario).where(SavedScenario.user_id == user_id)
                           .order_by(SavedScenario.created_at).limit(1))
        db.delete(oldest)
    row = SavedScenario(user_id=user_id, name=name[:120], params=s.to_dict(), summary=summary)
    db.add(row)
    db.flush()
    return row


def saved(db: Session, user_id: int) -> list[SavedScenario]:
    return list(db.scalars(select(SavedScenario).where(SavedScenario.user_id == user_id)
                           .order_by(SavedScenario.created_at.desc())))


# --- a scenario from a sentence ----------------------------------------------------- #

def _system(products: list[tuple[str, str]]) -> str:
    plist = "\n".join(f"  {c}: {n}" for c, n in products)
    return f"""You turn a banker's what-if question into ONE scenario for an FTP (funds transfer pricing) simulator at a Bangladeshi bank. You see no data.

Reply with a single JSON object, no prose:
{{"scenario": {{...fields...}}, "title": "a short title", "assumptions": ["each assumption you made, plainly"]}}

Fields (omit any you do not need; defaults in brackets):
  market_bp: parallel move in market rates / the policy rate, basis points [0]
  bench_follow: share of the market move the bank's FTP benchmarks follow, 0..1 [1.0]
  bench_follow_demand: the same for current & savings accounts [0.0]
  deposit_pass: share of the market move passed to term-deposit customers [0.5]
  demand_pass: share passed to current & savings customers [0.2]
  loan_pass: share passed to borrowers [0.7]
  competitor_bp: other banks raise deposit rates by this beyond the market move [0]
  product_rate_bp: {{"PRODUCT_CODE": bp}} change a product's customer rate
  product_bench_bp: {{"PRODUCT_CODE": bp}} change a product's FTP benchmark
  deposit_growth_pct, loan_growth_pct: balance change over the horizon, % [0]
  elasticity: % of term deposits lost per 100 bp we fall behind other banks [2.0]
  horizon_months: 1..24 [3]

Products (code: name):
{plist}

Rules: "policy rate"/"repo"/"BB raises" means market_bp. "Pass on X%" sets the pass shares. 1 percentage point = 100 bp. Use product codes exactly as listed. List every assumption."""


def from_text(db: Session, caller: Caller, text: str, scope_products: list[str] | None = None) -> dict:
    prods = [(p.product_code, p.short_name) for p in db.scalars(
        select(Product).where(Product.is_active.is_(True)).order_by(Product.side, Product.product_code))]
    req = GatewayRequest(purpose="scenario_parse", system=_system(prods),
                         turns=[Turn("user", [Segment("user", text.strip()[:600])])],
                         max_tokens=600, json_mode=True)
    r = gw.call(db, caller, req)
    raw = r.masked_text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`").removeprefix("json").strip()
    try:
        d = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
    except ValueError as exc:
        raise engine.ScenarioError("the reply was not a scenario") from exc
    s = engine.parse(d.get("scenario") or d, products={c for c, _ in prods})
    return {"scenario": s.to_dict(), "title": str(d.get("title") or "")[:120],
            "assumptions": [str(a)[:200] for a in (d.get("assumptions") or [])][:8],
            "provider": r.provider, "model": r.model}


def with_branches(s: engine.Scenario, codes: list[str]) -> engine.Scenario:
    return replace(s, branches=tuple(codes))

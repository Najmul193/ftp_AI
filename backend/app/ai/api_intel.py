"""HTTP routes for the copilot's intelligence: public data, outlook, scenarios.

Included by `app.ai.api` under the same `/ai` prefix and the same rules:
every route needs AI switched on, bank-wide figures are head office only.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.ai import jobs
from app.ai import settings_service as svc
from app.ai.copilot import tools
from app.ai.forecast import service as forecast
from app.ai.forecast import track
from app.ai.insights import engine
from app.ai.public import parse as public_parse
from app.ai.public import service as public
from app.ai.gateway import gateway as gw
from app.ai.gateway.gateway import Caller
from app.ai.scenario import engine as scenario_engine
from app.ai.scenario import service as scenario
from app.api.deps import CurrentUser, DbDep, ScopeDep, get_active_user, require
from app.domain.scope import ScopeViolation
from app.domain.types import ScopeLevel
from app.models import Branch

router = APIRouter(tags=["ai"])  # included by app.ai.api, which adds /ai

ActiveUser = Annotated[CurrentUser, Depends(get_active_user)]


def require_ai_enabled(db: DbDep) -> None:
    if not svc.state(db).enabled:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            {"code": "AI_DISABLED", "message": "AI features are switched off"})


_view = [Depends(require("AI_VIEW")), Depends(require_ai_enabled)]


def _market_editor(user: Annotated[CurrentUser, Depends(require("AI_MARKET_EDIT"))]) -> CurrentUser:
    if user.scope_level is not ScopeLevel.HO:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "restricted to head office")
    return user


MarketEditor = Annotated[CurrentUser, Depends(_market_editor)]


def _ho_admin(user: Annotated[CurrentUser, Depends(require("AI_ADMIN"))]) -> CurrentUser:
    if user.scope_level is not ScopeLevel.HO:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "restricted to head-office administrators")
    return user


HoAdmin = Annotated[CurrentUser, Depends(_ho_admin)]


# --- public data ----------------------------------------------------------------- #

@router.get("/public/peers", dependencies=_view)
def peers(db: DbDep, book: Literal["deposit", "lending"] = "deposit") -> dict:
    """Every bank's posted rates for the latest month, with where this bank stands.

    Public data: any AI viewer may see it."""
    return {**public.peer_table(db, book), "collected": public.last_collected(db)}


@router.get("/public/book-vs-peers", dependencies=_view)
def book_vs_peers(db: DbDep, user: ActiveUser, scope: ScopeDep) -> dict:
    """The asker's product rates and balances against the market's posted
    rates: head office sees the bank, a division its division, a branch its own."""
    return {"items": public.book_vs_peers(db, branch_ids=None if scope.unrestricted
                                          else list(scope.branch_ids or [])),
            "self_bank": public.meta(db)["self_bank"], "label": _scope_label(db, user)}


@router.get("/public/industry", dependencies=_view)
def industry(db: DbDep) -> dict:
    return public.industry(db)


@router.get("/public/macro", dependencies=_view)
def macro(db: DbDep) -> dict:
    m = public.meta(db)
    return {"series": public.macro(db), "last_mpc": m.get("last_mpc")}


@router.get("/public/peer-financials", dependencies=_view)
def peer_financials(db: DbDep) -> dict:
    return public.peer_financials(db)


class PeerPasteIn(BaseModel):
    text: str = Field(min_length=10, max_length=200_000)
    source: str | None = Field(None, max_length=300)


@router.post("/public/peer-financials/parse", dependencies=[Depends(require_ai_enabled)])
def peer_financials_parse(body: PeerPasteIn, _user: MarketEditor) -> dict:
    """Read pasted peer figures. Saves nothing."""
    figs, warnings = public_parse.peer_financials(body.text)
    return {"figures": [f.__dict__ for f in figs], "warnings": warnings}


@router.post("/public/peer-financials", dependencies=[Depends(require_ai_enabled)])
def peer_financials_save(body: PeerPasteIn, db: DbDep, user: MarketEditor) -> dict:
    figs, warnings = public_parse.peer_financials(body.text)
    if not figs:
        raise HTTPException(422, {"code": "nothing_read",
                                  "message": "; ".join(warnings) or "no figures found"})
    n = public.save_peer_financials(db, figs, user_id=user.id, ref=body.source)
    return {"saved": n, "warnings": warnings}


class SelfBankIn(BaseModel):
    bank: str = Field(min_length=2, max_length=40)


@router.put("/public/self-bank", dependencies=[Depends(require_ai_enabled)])
def set_self_bank(body: SelfBankIn, db: DbDep, user: HoAdmin) -> dict:
    """Which row of Bangladesh Bank's tables is this bank."""
    known = {b["bank"] for b in public.peer_table(db, "deposit")["banks"]}
    if known and body.bank.strip().upper() not in {k.upper() for k in known}:
        raise HTTPException(422, {"code": "unknown_bank",
                                  "message": f"{body.bank} is not in Bangladesh Bank's tables"})
    return public.set_self_bank(db, body.bank, actor_id=user.id, actor_username=user.username)


@router.post("/public/refresh", dependencies=[Depends(require_ai_enabled)])
def refresh(_user: MarketEditor) -> dict:
    """Read Bangladesh Bank's public tables and the macro sources now."""
    r = jobs.run_job("public_data", "manual")
    if r.get("status") in ("ok", "partial"):
        jobs.run_job("insights", "event")
    return r


# --- outlook: forecasts ------------------------------------------------------------- #

@router.get("/outlook/market", dependencies=_view)
def outlook_market(db: DbDep) -> dict:
    """Market rates 90 days ahead with bands. Public data."""
    return forecast.market_outlook(db)


@router.get("/outlook/policy", dependencies=_view)
def outlook_policy(db: DbDep) -> dict:
    """Which way the policy rate leans at the next MPC meeting, and why."""
    return forecast.policy_outlook(db)


@router.get("/outlook/book", dependencies=_view)
def outlook_book(db: DbDep, user: ActiveUser, scope: ScopeDep,
                 branch: str | None = Query(None, max_length=20)) -> dict:
    """The asker's book to month-end, quarter-end and 90 days, with bands.

    `branch` narrows to one branch inside the asker's scope; outside it the
    platform's own scope check refuses, as on every dashboard."""
    b = db.scalar(select(Branch).where(Branch.branch_code == branch)) if branch else None
    if branch and b is None:
        # An unknown code would otherwise filter nothing and show the whole scope.
        raise HTTPException(404, f"no branch {branch}")
    where = tools.Where(branch_codes=(branch,)) if branch else tools.Where()
    label = f"{b.branch_name} ({b.branch_code})" if b else _scope_label(db, user)
    try:
        return forecast.book_outlook(db, forecast.BookScope(scope, where, label))
    except ScopeViolation as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "that branch is outside your scope") from exc


@router.get("/outlook/track-record", dependencies=_view)
def outlook_track_record(db: DbDep, user: ActiveUser) -> dict:
    keys = [k for k in engine.visible_keys(user.scope_level, user.scope_id) if k != "PUBLIC"]
    return track.record(db, keys)


def _scope_label(db, user: CurrentUser) -> str:
    """The asker's part of the bank by name."""
    from app.models import District, Division
    if user.scope_level is ScopeLevel.HO:
        return "Whole bank"
    model = {ScopeLevel.DIVISION: Division, ScopeLevel.DISTRICT: District}.get(user.scope_level)
    if model is not None:
        row = db.get(model, user.scope_id)
        return f"{row.name} {user.scope_level.value.lower()}" if row else "Your area"
    b = db.get(Branch, user.scope_id)
    return f"{b.branch_name} ({b.branch_code})" if b else "Your branch"


# --- scenario lab ----------------------------------------------------------------------- #

_scenario = [Depends(require("SCENARIO_RUN")), Depends(require_ai_enabled)]


def _scope_branches(db, scope) -> set[str]:
    q = select(Branch.branch_code)
    if not scope.unrestricted:
        q = q.where(Branch.id.in_(scope.branch_ids or [-1]))
    return set(db.scalars(q))


@router.get("/scenario/presets", dependencies=_scenario)
def scenario_presets(db: DbDep, user: ActiveUser) -> dict:
    return {"items": scenario.presets(db, head_office=user.scope_level is ScopeLevel.HO),
            "defaults": scenario_engine.Scenario().to_dict(), "limits": scenario_engine.LIMITS}


class ScenarioIn(BaseModel):
    scenario: dict


@router.post("/scenario/run", dependencies=_scenario)
def scenario_run(body: ScenarioIn, db: DbDep, scope: ScopeDep) -> dict:
    """Run a what-if on the asker's part of the book. Code only: no model."""
    try:
        s = scenario_engine.parse(body.scenario, branches=_scope_branches(db, scope))
    except scenario_engine.ScenarioError as exc:
        raise HTTPException(422, {"code": "bad_scenario", "message": str(exc)}) from exc
    return scenario.run(db, scope, s)


class ScenarioTextIn(BaseModel):
    text: str = Field(min_length=5, max_length=600)


@router.post("/scenario/parse", dependencies=[*_scenario, Depends(require("AI_CHAT"))])
def scenario_parse(body: ScenarioTextIn, db: DbDep, user: ActiveUser) -> dict:
    """A scenario read out of a sentence by the model, checked field by field.
    The model sees the question and the product list, never the book."""
    try:
        return scenario.from_text(db, Caller(user.id, user.username), body.text)
    except scenario_engine.ScenarioError as exc:
        raise HTTPException(422, {"code": "bad_scenario",
                                  "message": f"I could not read a scenario from that: {exc}"}) from exc
    except (gw.AiUnavailable, gw.GatewayBlocked, gw.GatewayError) as exc:
        raise HTTPException(502, {"code": getattr(exc, "code", "provider"),
                                  "message": getattr(exc, "message", str(exc))}) from exc


class SaveScenarioIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    scenario: dict
    summary: dict = Field(default_factory=dict)


@router.get("/scenario/saved", dependencies=_scenario)
def scenario_saved(db: DbDep, user: ActiveUser) -> dict:
    return {"items": [{"id": s.id, "name": s.name, "scenario": s.params, "summary": s.summary,
                       "created_at": s.created_at} for s in scenario.saved(db, user.id)]}


@router.post("/scenario/saved", dependencies=_scenario)
def scenario_save(body: SaveScenarioIn, db: DbDep, user: ActiveUser) -> dict:
    try:
        s = scenario_engine.parse(body.scenario)
    except scenario_engine.ScenarioError as exc:
        raise HTTPException(422, {"code": "bad_scenario", "message": str(exc)}) from exc
    row = scenario.save(db, user.id, body.name, s, body.summary)
    return {"id": row.id}


@router.delete("/scenario/saved/{sid}", status_code=204, dependencies=_scenario)
def scenario_delete(sid: int, db: DbDep, user: ActiveUser) -> None:
    from app.ai.models import SavedScenario
    row = db.get(SavedScenario, sid)
    if row is None or row.user_id != user.id:
        raise HTTPException(404, "not found")
    db.delete(row)


# --- the bank's pulse ----------------------------------------------------------------------- #

@router.get("/pulse", dependencies=_view)
def bank_pulse(db: DbDep, user: ActiveUser, scope: ScopeDep) -> dict:
    """One score and its parts, against the industry, in the asker's scope."""
    from app.ai import pulse
    from app.ai.context.fact_sheet import names as org_names
    from app.ai.insights.facts import fill_names
    out = pulse.build(db, scope, engine.Reader(user.id, user.scope_level, user.scope_id),
                      _scope_label(db, user))
    names = org_names(db)
    for k in ("risks", "openings"):
        for i in out.get(k, []):
            i["title"] = fill_names(i["title"], names)
    return out


# --- the ALCO pack --------------------------------------------------------------------------- #

@router.get("/alco", dependencies=[*_view, Depends(require("SCENARIO_RUN"))])
def alco_pack(db: DbDep, user: ActiveUser, scope: ScopeDep) -> dict:
    from app.ai import alco
    return alco.build(db, scope, engine.Reader(user.id, user.scope_level, user.scope_id),
                      head_office=user.scope_level is ScopeLevel.HO, label=_scope_label(db, user))


@router.post("/alco/commentary",
             dependencies=[*_view, Depends(require("SCENARIO_RUN")), Depends(require("AI_CHAT"))])
def alco_commentary(db: DbDep, user: ActiveUser, scope: ScopeDep) -> dict:
    """The model writes the commentary from the masked figures; every number checked."""
    from app.ai import alco
    try:
        return alco.commentary(db, Caller(user.id, user.username), scope)
    except (gw.AiUnavailable, gw.GatewayBlocked, gw.GatewayError) as exc:
        raise HTTPException(502, {"code": getattr(exc, "code", "provider"),
                                  "message": getattr(exc, "message", str(exc))}) from exc


# --- market rate explorer ---------------------------------------------------------------- #

def _branch_ids(scope) -> list[int] | None:
    return None if scope.unrestricted else list(scope.branch_ids or [])


PeerSet = Literal["competitors", "pcb", "fb", "scb", "islamic", "all"]


@router.get("/market/search", dependencies=_view)
def market_search(db: DbDep, q: str = Query(..., min_length=1, max_length=80)) -> dict:
    from app.ai.public import explorer
    return {"items": explorer.search(db, q)}


@router.get("/market/grid", dependencies=_view)
def market_grid(db: DbDep, scope: ScopeDep, book: Literal["deposit", "lending"] = "deposit",
                peers: PeerSet = "competitors") -> dict:
    """Every bank's posted rates by category; our bank's row and our book's
    actual rates (in the asker's scope) pinned."""
    from app.ai.public import explorer
    return explorer.grid(db, book, peers, _branch_ids(scope))


@router.get("/market/category", dependencies=_view)
def market_category(db: DbDep, scope: ScopeDep, product: str = Query(..., max_length=20),
                    book: Literal["deposit", "lending"] = "deposit",
                    peers: PeerSet = "competitors") -> dict:
    from app.ai.public import explorer
    return explorer.category(db, book, product, peers, _branch_ids(scope))


@router.get("/market/bank/{code}", dependencies=_view)
def market_bank(code: str, db: DbDep, scope: ScopeDep) -> dict:
    from app.ai.public import explorer
    out = explorer.bank(db, code, _branch_ids(scope))
    if not out.get("available"):
        raise HTTPException(404, f"no bank {code}")
    return out


@router.get("/market/movers", dependencies=_view)
def market_movers(db: DbDep, peers: PeerSet = "all") -> dict:
    from app.ai.public import explorer
    return explorer.movers(db, peers)


@router.get("/market/products", dependencies=_view)
def market_products(db: DbDep, user: ActiveUser, scope: ScopeDep) -> dict:
    """Our products -- every active one, new ones included -- with the market
    category each is compared with, against the market and our competitors.
    Follows the product master: an added or retired product shows at once."""
    from app.ai.public import banks as bank_dir
    from app.ai.public import parse as pp
    comp = public.competitors(db)
    d = public.directory(db)
    return {"items": public.book_vs_peers(db, branch_ids=_branch_ids(scope), include_new=True),
            "unmapped": _unmapped(db), "label": _scope_label(db, user),
            "categories": [{"value": f"{'deposit' if p in pp.DEPOSIT_PRODUCTS else 'lending'}:{p}",
                            "label": lab} for p, lab in pp.PRODUCT_LABELS.items()],
            "competitors": [{"code": c, "name": bank_dir.bank_of(c, d).name} for c in comp],
            "banks": [{"code": b.code, "name": b.name, "group": b.group} for b in
                      sorted(d.values(), key=lambda b: b.name)],
            "self_bank": public.meta(db)["self_bank"]}


def _unmapped(db) -> list[dict]:
    """Active products with no market line to compare with (current accounts,
    or set to "none")."""
    from app.models import Product as P
    overrides = public.product_map(db)
    out = []
    for p in db.scalars(select(P).where(P.is_active.is_(True)).order_by(P.side, P.product_code)):
        key, source = public.category_of(p, overrides)
        if key is None:
            out.append({"product_code": p.product_code, "name": p.short_name, "side": p.side.value,
                        "mapping": source})
    return out


class ProductMapIn(BaseModel):
    product_code: str = Field(min_length=1, max_length=40)
    #: "deposit:fd_1y" / "lending:housing", "none" to leave it uncompared, null for automatic.
    category: str | None = Field(None, max_length=40)


@router.put("/market/product-map", dependencies=[Depends(require_ai_enabled)])
def market_product_map(body: ProductMapIn, db: DbDep, user: MarketEditor) -> dict:
    from app.models import Product as P
    if db.scalar(select(P.id).where(P.product_code == body.product_code)) is None:
        raise HTTPException(404, f"no product {body.product_code}")
    try:
        m = public.set_product_map(db, body.product_code, body.category, actor_id=user.id,
                                   actor_username=user.username)
    except ValueError as exc:
        raise HTTPException(422, {"code": "bad_category", "message": str(exc)}) from exc
    return {"product_map": m}


class CompetitorsIn(BaseModel):
    banks: list[str] = Field(default_factory=list, max_length=30)


@router.put("/market/competitors", dependencies=[Depends(require_ai_enabled)])
def market_competitors(body: CompetitorsIn, db: DbDep, user: HoAdmin) -> dict:
    try:
        return {"competitors": public.set_competitors(db, body.banks, actor_id=user.id,
                                                      actor_username=user.username)}
    except ValueError as exc:
        raise HTTPException(422, {"code": "bad_bank", "message": str(exc)}) from exc

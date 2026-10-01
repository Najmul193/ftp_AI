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


def _head_office(user: Annotated[CurrentUser, Depends(require("AI_VIEW"))]) -> CurrentUser:
    if user.scope_level is not ScopeLevel.HO:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "bank-wide figures are for head office")
    return user


HeadOffice = Annotated[CurrentUser, Depends(_head_office)]


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


@router.get("/public/book-vs-peers", dependencies=[Depends(require_ai_enabled)])
def book_vs_peers(db: DbDep, _user: HeadOffice) -> dict:
    """This bank's own product rates and balances against the market's posted
    rates: bank-wide balances, so head office only."""
    return {"items": public.book_vs_peers(db), "self_bank": public.meta(db)["self_bank"]}


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

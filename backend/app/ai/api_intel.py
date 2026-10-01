"""HTTP routes for the copilot's intelligence: public data, outlook, scenarios.

Included by `app.ai.api` under the same `/ai` prefix and the same rules:
every route needs AI switched on, bank-wide figures are head office only.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.ai import jobs
from app.ai import settings_service as svc
from app.ai.public import parse as public_parse
from app.ai.public import service as public
from app.api.deps import CurrentUser, DbDep, get_active_user, require
from app.domain.types import ScopeLevel

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

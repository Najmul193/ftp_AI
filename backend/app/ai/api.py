"""HTTP routes for the AI module, under `/api/v1/ai`.

Administration works while AI is switched off -- that is when it is set up.
Every feature route depends on `require_ai_enabled`, and the gateway re-checks
the switch on every call, so turning AI off takes effect immediately for work
already under way.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.ai import settings_service as svc
from app.ai.context.entities import user_vault
from app.ai.gateway import gateway as gw
from app.ai.gateway.gateway import (
    AiUnavailable, Caller, GatewayBlocked, GatewayError, GatewayRequest, Segment, Turn,
)
from app.ai.gateway.policy import ALLOWED, HARD_DROP, PolicyViolation
from app.ai.models import AiProvider
from app.ai.presets import PRESETS, presets_public, validate_base_url
from app.api.deps import CurrentUser, DbDep, get_active_user, require
from app.core.config import settings as core
from app.domain.types import ScopeLevel

router = APIRouter(prefix="/ai", tags=["ai"])

ActiveUser = Annotated[CurrentUser, Depends(get_active_user)]


def _actor(user: CurrentUser) -> svc.Actor:
    return svc.Actor(user.id, user.username,
                     f"{user.scope_level.value}:{user.scope_id or ''}")


def _caller(user: CurrentUser) -> Caller:
    return Caller(user.id, user.username)


def _ho_admin(user: Annotated[CurrentUser, Depends(require("AI_ADMIN"))]) -> CurrentUser:
    """AI configuration decides what leaves the bank: head office only."""
    if user.scope_level is not ScopeLevel.HO:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "AI configuration is restricted to head-office administrators")
    return user


HoAdmin = Annotated[CurrentUser, Depends(_ho_admin)]


def require_ai_enabled(db: DbDep) -> None:
    s = svc.state(db)
    if not s.enabled:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            {"code": "AI_DISABLED", "message": "AI features are switched off"})


def _fail(exc: Exception) -> HTTPException:
    if isinstance(exc, svc.AiConfigError):
        return HTTPException(exc.status, {"code": exc.code, "message": exc.message})
    if isinstance(exc, PolicyViolation):
        return HTTPException(422, {"code": exc.code, "message": exc.message})
    if isinstance(exc, AiUnavailable):
        return HTTPException(409, {"code": exc.code, "message": exc.message})
    if isinstance(exc, GatewayBlocked):
        return HTTPException(422, {"code": f"blocked_{exc.code}", "message": exc.message,
                                   "request_id": exc.request_id})
    if isinstance(exc, GatewayError):
        return HTTPException(502, {"code": f"provider_{exc.code}", "message": exc.message,
                                   "request_id": exc.request_id, "retryable": exc.retryable})
    raise exc


# --- status (any signed-in user) --------------------------------------------- #

@router.get("/status")
def ai_status(db: DbDep, user: ActiveUser) -> dict:
    """What the UI needs to decide whether to show AI at all."""
    s = svc.state(db)
    active = db.get(AiProvider, s.active_provider_id) if s.active_provider_id else None
    return {
        "module": True,
        "enabled": s.enabled,
        "provider": ({"brand": active.brand, "label": active.label, "model": active.model}
                     if active else None),
        "can_view": s.enabled and user.has("AI_VIEW"),
        "can_chat": s.enabled and user.has("AI_CHAT"),
        "can_admin": user.has("AI_ADMIN") and user.scope_level is ScopeLevel.HO,
        "can_audit": user.has("AI_AUDIT"),
    }


# --- administration ---------------------------------------------------------- #

@router.get("/presets", dependencies=[Depends(require("AI_ADMIN"))])
def presets() -> list[dict]:
    return presets_public()


@router.get("/settings", dependencies=[Depends(require("AI_ADMIN"))])
def get_settings(db: DbDep) -> dict:
    s = svc.state(db)
    provs = svc.list_providers(db)
    usage = gw.usage_today(db, [p.id for p in provs])
    return {
        "enabled": s.enabled,
        "active_provider_id": s.active_provider_id,
        "policy": s.policy.to_dict(),
        "policy_options": {k: list(v) for k, v in ALLOWED.items()},
        "hard_drop": sorted(HARD_DROP),
        "bank_tier": s.policy.bank_tier.name,
        "providers": [{**svc.public(p), "tokens_today": usage.get(p.id, 0)} for p in provs],
    }


class EnabledIn(BaseModel):
    enabled: bool


@router.put("/settings/enabled")
def set_enabled(body: EnabledIn, db: DbDep, user: HoAdmin) -> dict:
    try:
        s = svc.set_enabled(db, _actor(user), body.enabled)
    except svc.AiConfigError as exc:
        raise _fail(exc) from exc
    return {"enabled": s.enabled}


class PolicyIn(BaseModel):
    actions: dict[str, str] = Field(default_factory=dict)
    amount_mode: Literal["crore_3sf", "index"] = "crore_3sf"


@router.put("/settings/policy")
def set_policy(body: PolicyIn, db: DbDep, user: HoAdmin) -> dict:
    try:
        s = svc.set_policy(db, _actor(user), body.model_dump())
    except PolicyViolation as exc:
        raise _fail(exc) from exc
    return {"policy": s.policy.to_dict(), "bank_tier": s.policy.bank_tier.name}


class ProbeIn(BaseModel):
    brand: str
    base_url: str | None = None
    api_key: str | None = Field(None, max_length=500)
    model: str | None = None


@router.post("/providers/probe")
def probe(body: ProbeIn, user: HoAdmin) -> dict:
    """Check a key before saving it: list its models, ping one if named."""
    preset = PRESETS.get(body.brand)
    if preset is None:
        raise HTTPException(422, {"code": "bad_brand", "message": f"unknown provider {body.brand!r}"})
    try:
        url = validate_base_url(body.base_url or preset.base_url, production=core.is_production)
    except ValueError as exc:
        raise HTTPException(422, {"code": "bad_url", "message": str(exc)}) from exc
    r = gw.probe(caller=_caller(user), brand=body.brand, kind=preset.kind, base_url=url,
                 api_key=body.api_key, model=body.model)
    return r.__dict__


class ProviderIn(BaseModel):
    brand: str
    model: str = Field(min_length=1, max_length=120)
    api_key: str | None = Field(None, max_length=500)
    base_url: str | None = None
    label: str | None = Field(None, max_length=120)
    max_data_tier: Literal["PUBLIC", "AGGREGATE", "RESTRICTED"] = "PUBLIC"
    is_free_tier: bool | None = None
    trial_ack: bool = False
    daily_token_budget: int = Field(0, ge=0)


@router.post("/providers", status_code=201)
def create_provider(body: ProviderIn, db: DbDep, user: HoAdmin) -> dict:
    try:
        p = svc.create_provider(db, _actor(user), **body.model_dump())
    except svc.AiConfigError as exc:
        raise _fail(exc) from exc
    test = gw.test_provider(db, _caller(user), p)
    return {"provider": svc.public(p), "test": test.__dict__}


class ProviderPatch(BaseModel):
    model: str | None = Field(None, max_length=120)
    api_key: str | None = Field(None, max_length=500)
    label: str | None = Field(None, max_length=120)
    max_data_tier: Literal["PUBLIC", "AGGREGATE", "RESTRICTED"] | None = None
    is_free_tier: bool | None = None
    trial_ack: bool = False
    daily_token_budget: int | None = Field(None, ge=0)


@router.patch("/providers/{pid}")
def update_provider(pid: int, body: ProviderPatch, db: DbDep, user: HoAdmin) -> dict:
    try:
        p = svc.update_provider(db, _actor(user), pid, **body.model_dump())
    except svc.AiConfigError as exc:
        raise _fail(exc) from exc
    return {"provider": svc.public(p)}


@router.delete("/providers/{pid}", status_code=204)
def delete_provider(pid: int, db: DbDep, user: HoAdmin) -> None:
    try:
        svc.delete_provider(db, _actor(user), pid)
    except svc.AiConfigError as exc:
        raise _fail(exc) from exc


@router.post("/providers/{pid}/test")
def test_provider(pid: int, db: DbDep, user: HoAdmin) -> dict:
    try:
        p = svc.get_provider(db, pid)
    except svc.AiConfigError as exc:
        raise _fail(exc) from exc
    r = gw.test_provider(db, _caller(user), p)
    return {"provider": svc.public(p), "test": r.__dict__}


@router.post("/providers/{pid}/activate")
def activate(pid: int, db: DbDep, user: HoAdmin) -> dict:
    try:
        s = svc.activate_provider(db, _actor(user), pid)
    except svc.AiConfigError as exc:
        raise _fail(exc) from exc
    return {"active_provider_id": s.active_provider_id, "enabled": s.enabled}


# --- try the gateway --------------------------------------------------------- #

class TryIn(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)


_TRY_SYSTEM = (
    "You are FTP Intelligence, an assistant inside a Bangladeshi bank's Funds Transfer "
    "Pricing platform. Identifiers such as BR_XXX (branch), DIST_XXX (district) and "
    "DIV_XXX (division) are opaque tokens for real names you are not shown: use them "
    "exactly as written and never guess what they stand for. Be concise and practical."
)


@router.post("/try", dependencies=[Depends(require("AI_CHAT")), Depends(require_ai_enabled)])
def try_gateway(body: TryIn, db: DbDep, user: ActiveUser) -> dict:
    """One question through the full gateway, showing what actually left."""
    s = svc.state(db)
    vault, codes = user_vault(db, s.policy)
    masked = vault.mask_text(body.prompt)
    req = GatewayRequest(purpose="try", system=_TRY_SYSTEM,
                         turns=[Turn("user", [Segment("user", masked)])],
                         vault=vault, numeric_codes=codes, max_tokens=800)
    try:
        r = gw.call(db, _caller(user), req)
    except (AiUnavailable, GatewayBlocked, GatewayError) as exc:
        raise _fail(exc) from exc
    return {"you_typed": body.prompt, "sent": masked, "answer": r.text,
            "provider_answer": r.masked_text, "tier": r.tier, "provider": r.provider,
            "model": r.model, "request_id": r.request_id}


# --- egress log -------------------------------------------------------------- #

@router.get("/egress-log", dependencies=[Depends(require("AI_AUDIT"))])
def egress_log(db: DbDep, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
               status_: str | None = Query(None, alias="status"),
               purpose: str | None = None) -> dict:
    rows, total = gw.recent_requests(db, limit=limit, offset=offset, status=status_,
                                     purpose=purpose)
    return {"total": total, "items": [{
        "id": r.id, "created_at": r.created_at.isoformat(), "username": r.username,
        "purpose": r.purpose, "provider": r.provider_label, "model": r.model,
        "tier": ["PUBLIC", "AGGREGATE", "RESTRICTED"][r.tier], "status": r.status,
        "blocked_reason": r.blocked_reason, "payload": r.masked_payload,
        "response": r.masked_response, "grounded": r.grounded, "unverified": r.unverified,
        "tokens_in": r.tokens_in, "tokens_out": r.tokens_out, "latency_ms": r.latency_ms,
    } for r in rows]}

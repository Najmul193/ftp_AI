"""HTTP routes for the AI module, under `/api/v1/ai`.

Administration works while AI is switched off -- that is when it is set up.
Every feature route depends on `require_ai_enabled`, and the gateway re-checks
the switch on every call, so turning AI off takes effect immediately for work
already under way.
"""

from __future__ import annotations

import secrets
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.ai import jobs
from app.ai import settings_service as svc
from app.ai.config import ai_settings
from app.ai.market import catalog, paste
from app.ai.market import service as market
from app.ai.context.entities import user_vault
from app.ai.context.fact_sheet import fingerprint as data_fingerprint
from app.ai.context.fact_sheet import names as org_names
from app.ai.insights import engine
from app.ai.insights.facts import fill_names
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


def _reader(user: CurrentUser) -> engine.Reader:
    return engine.Reader(user.id, user.scope_level, user.scope_id)


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
        # Rides the 15-second status poll, so the bell needs no poll of its own.
        "unread": (engine.unread_count(db, _reader(user))
                   if s.enabled and user.has("AI_VIEW") else 0),
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


# --- market data ------------------------------------------------------------- #

_view = [Depends(require("AI_VIEW")), Depends(require_ai_enabled)]


def _market_editor(user: Annotated[CurrentUser, Depends(require("AI_MARKET_EDIT"))]) -> CurrentUser:
    """Market rates move every FTP benchmark comparison: head office only."""
    if user.scope_level is not ScopeLevel.HO:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "entering market rates is restricted to head office")
    return user


MarketEditor = Annotated[CurrentUser, Depends(_market_editor)]


@router.get("/market/overview", dependencies=_view)
def market_overview(db: DbDep, user: ActiveUser) -> dict:
    # Product balances are bank-wide figures: head office only.
    return {**market.overview(db, include_balances=user.scope_level is ScopeLevel.HO),
            "jobs": jobs.status(db)}


@router.get("/market/series/{code}", dependencies=_view)
def market_series(code: str, db: DbDep, days: int = Query(365, ge=7, le=3650)) -> dict:
    if code not in catalog.BY_CODE:
        raise HTTPException(404, f"unknown series {code}")
    s = catalog.BY_CODE[code]
    return {"code": code, "name": s.name, "unit": s.unit,
            "points": [{"date": o.obs_date, "value": o.value, "source": o.source}
                       for o in market.series_history(db, code, days)]}


@router.get("/market/news", dependencies=_view)
def market_news(db: DbDep, tag: str | None = None, region: Literal["BD", "GLOBAL"] | None = None,
                min_relevance: int = Query(1, ge=0, le=20), q: str | None = Query(None, max_length=100),
                limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0),
                sort: Literal["top", "latest"] = "top") -> dict:
    rows, total = market.news(db, tag_=tag, region=region, min_relevance=min_relevance,
                              limit=limit, offset=offset, q=q, sort=sort)
    return {"total": total, "items": [{
        "id": n.id, "source": n.source, "title": n.title, "url": n.url,
        "published_at": n.published_at, "summary": n.summary, "region": n.region,
        "tags": n.tags, "impacts": n.impacts, "rate_signal": n.rate_signal,
        "relevance": n.relevance} for n in rows]}


class PasteIn(BaseModel):
    text: str = Field(min_length=10, max_length=200_000)


@router.post("/market/parse", dependencies=[Depends(require_ai_enabled)])
def market_parse(body: PasteIn, db: DbDep, _user: MarketEditor) -> dict:
    """Read rates out of a pasted Bangladesh Bank page. Saves nothing."""
    r = paste.parse(body.text)
    current = {s["code"]: s for s in market.overview(db, include_balances=False)["series"]}
    return {"kind": r.kind, "page_date": r.page_date, "warnings": r.warnings, "items": [{
        "code": i.code, "name": catalog.BY_CODE[i.code].name, "unit": catalog.BY_CODE[i.code].unit,
        "obs_date": i.obs_date or r.page_date, "value": i.value, "evidence": i.evidence,
        "current": current.get(i.code, {}).get("value"),
        "current_as_of": current.get(i.code, {}).get("as_of"),
    } for i in r.items]}


class EntryIn(BaseModel):
    code: str
    obs_date: date
    value: Decimal = Field(gt=0, lt=100_000_000)
    ref: str | None = Field(None, max_length=500)


class EntriesIn(BaseModel):
    source: Literal["bb_paste", "manual"]
    entries: list[EntryIn] = Field(min_length=1, max_length=50)


@router.post("/market/entries", dependencies=[Depends(require_ai_enabled)])
def market_entries(body: EntriesIn, db: DbDep, user: MarketEditor,
                   background: BackgroundTasks) -> dict:
    today = date.today()
    for e in body.entries:
        s = catalog.BY_CODE.get(e.code)
        if s is None or e.code not in catalog.MANUAL:
            raise HTTPException(422, {"code": "bad_series", "message": f"{e.code} cannot be entered"})
        if e.obs_date > today or (today - e.obs_date).days > 400:
            raise HTTPException(422, {"code": "bad_date",
                                      "message": f"{s.short}: date {e.obs_date} is out of range"})
        if s.unit == "pct" and not (Decimal("0.01") <= e.value <= Decimal("40")):
            raise HTTPException(422, {"code": "bad_value",
                                      "message": f"{s.short}: {e.value}% is not a plausible rate"})
    try:
        changed = market.save_entries(db, user.id, [e.model_dump() for e in body.entries],
                                      body.source)
    except ValueError as exc:
        raise HTTPException(422, {"code": "bad_series", "message": str(exc)}) from exc
    if changed:
        # After the commit: new rates can raise or clear insights straight away.
        background.add_task(jobs.run_job, "insights", "event")
    return {"saved": len(body.entries), "changed": changed}


@router.post("/market/refresh", dependencies=[Depends(require_ai_enabled)])
def market_refresh(_user: HoAdmin) -> dict:
    """Collect prices and news now instead of waiting for the schedule."""
    return {"results": [jobs.run_job("market_bb", "manual"),
                        jobs.run_job("market_prices", "manual"),
                        jobs.run_job("market_news", "manual"),
                        jobs.run_job("insights", "manual")]}


@router.post("/jobs/tick", include_in_schema=False)
def jobs_tick(db: DbDep, x_ai_jobs_token: Annotated[str | None, Header()] = None) -> dict:
    """For an external cron: run whatever is due. Token-guarded, no user."""
    expected = ai_settings().AI_JOBS_TOKEN
    if not expected or not x_ai_jobs_token or not secrets.compare_digest(x_ai_jobs_token, expected):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "bad or missing job token")
    due = jobs.due_jobs(db)
    return {"ran": [jobs.run_job(j, "tick") for j in due]}


# --- insights ---------------------------------------------------------------- #

def _insight_out(i, m, names: dict[str, str]) -> dict:
    return {
        "id": i.id, "kind": i.kind, "subject": i.subject, "scope": i.scope_key,
        "severity": i.severity, "title": fill_names(i.title, names),
        "body": fill_names(i.body, names), "money_at_stake": i.money_at_stake,
        "money_basis": i.money_basis, "evidence": i.evidence, "action": i.action,
        "sources": i.sources, "business_date": i.business_date, "status": i.status,
        "raised_at": i.raised_at, "last_seen_at": i.last_seen_at, "resolved_at": i.resolved_at,
        "read": bool(m and m.read_at and m.read_at >= i.raised_at),
        "useful": m.useful if m else None,
        "dismissed": bool(m and m.dismissed_at and m.dismissed_at >= i.raised_at),
    }


@router.get("/insights", dependencies=_view)
def insights(db: DbDep, user: ActiveUser,
             status_: Literal["active", "resolved"] = Query("active", alias="status"),
             include_dismissed: bool = False, limit: int = Query(100, ge=1, le=300)) -> dict:
    rows = engine.feed(db, _reader(user), status=status_, include_dismissed=include_dismissed,
                       limit=limit)
    names = org_names(db)
    return {"items": [_insight_out(i, m, names) for i, m in rows],
            "unread": engine.unread_count(db, _reader(user))}


class MarkIn(BaseModel):
    ids: list[int] = Field(default_factory=list, max_length=300)
    #: Mark every visible active insight read.
    all: bool = False


@router.post("/insights/read", dependencies=_view)
def insights_read(body: MarkIn, db: DbDep, user: ActiveUser) -> dict:
    r = _reader(user)
    ids = engine.unread_ids(db, r) if body.all else [
        i for i in body.ids if engine.get_visible(db, r, i) is not None]
    if ids:
        engine.mark(db, r, ids, read=True)
    return {"marked": len(ids)}


class FeedbackIn(BaseModel):
    useful: bool | None = None
    dismissed: bool | None = None


@router.post("/insights/{iid}/feedback", dependencies=_view)
def insight_feedback(iid: int, body: FeedbackIn, db: DbDep, user: ActiveUser) -> dict:
    r = _reader(user)
    if engine.get_visible(db, r, iid) is None:
        raise HTTPException(404, "no such insight")
    engine.mark(db, r, [iid], read=True, useful=body.useful, dismissed=body.dismissed)
    return {"ok": True}


@router.post("/insights/refresh", dependencies=_view)
def insights_refresh(user: ActiveUser) -> dict:
    """Run the detectors now. Head office only: it re-reads the whole book."""
    if user.scope_level is not ScopeLevel.HO:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "refreshing insights is head office only")
    return jobs.run_job("insights", "manual", force=True)


# --- morning brief ----------------------------------------------------------- #

def _brief_out(db, user: CurrentUser, lang: str) -> dict:
    key = engine.scope_key_for(user.scope_level, user.scope_id)
    fs, _found, std = engine.standard(db, key)
    names = fs.names
    visible = {(i.kind, i.subject): i.id for i, _ in engine.feed(db, _reader(user), limit=300)}
    std = {
        **std,
        "headline": fill_names(std["headline"], names),
        "sections": [{**s, "title": fill_names(s["title"], names),
                      "lines": [fill_names(x, names) for x in s["lines"]]} for s in std["sections"]],
        "decisions": [{**d, "title": fill_names(d["title"], names),
                       "insight_id": visible.get((d["kind"], d["subject"]))}
                      for d in std["decisions"]],
    }
    b = engine.stored(db, key, lang)
    s = svc.state(db)
    return {
        "scope": key, "scope_label": fs.scope_label, "lang": lang, "standard": std,
        "ai": None if b is None else {
            "narrative": b.narrative, "grounded": b.grounded, "unverified": b.unverified or [],
            "provider": b.provider, "model": b.model, "created_at": b.created_at,
            "request_id": b.request_id, "sent": b.sent,
            # Written before the latest upload, rate or market change.
            "current": b.fingerprint == data_fingerprint(db, news=False),
        },
        "can_write": s.enabled and s.active_provider_id is not None,
        "can_rewrite": s.enabled and s.active_provider_id is not None and user.has("AI_CHAT"),
    }


@router.get("/brief", dependencies=_view)
def get_brief(db: DbDep, user: ActiveUser, lang: Literal["en", "bn"] = "en") -> dict:
    return _brief_out(db, user, lang)


class BriefIn(BaseModel):
    lang: Literal["en", "bn"] = "en"
    #: Write it again although today's write-up is current.
    rewrite: bool = False


@router.post("/brief/write", dependencies=_view)
def write_brief(body: BriefIn, db: DbDep, user: ActiveUser) -> dict:
    """Have the active provider write the brief up. One write-up per scope,
    day and language is shared by everyone who reads it; writing it again
    while it is still current needs AI_CHAT."""
    key = engine.scope_key_for(user.scope_level, user.scope_id)
    have = engine.stored(db, key, body.lang)
    if have is not None and have.fingerprint == data_fingerprint(db, news=False):
        if not body.rewrite:
            return _brief_out(db, user, body.lang)
        if not user.has("AI_CHAT"):
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                "today's write-up is current; rewriting it needs AI_CHAT")
    try:
        engine.narrate(db, _caller(user), key, body.lang, user.id)
    except engine.BriefUnavailable as exc:
        raise HTTPException(422, {"code": exc.code, "message": exc.message}) from exc
    except (AiUnavailable, GatewayBlocked, GatewayError) as exc:
        raise _fail(exc) from exc
    return _brief_out(db, user, body.lang)

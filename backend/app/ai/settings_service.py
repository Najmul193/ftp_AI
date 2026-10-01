"""The master switch, the field policy, and the connected providers.

Configuration changes are audited in `audit_log`, in the same transaction as
the change, like every other change in the platform. Keys never appear in an
audit record: only "set" / "replaced" and the last four characters.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai import crypto
from app.ai.gateway.policy import FieldPolicy, Tier
from app.ai.models import AiProvider
from app.ai.presets import PRESETS, validate_base_url
from app.ai.providers.base import agentic_default
from app.core.config import settings as core
from app.models import SystemSetting
from app.services import audit

SETTINGS_KEY = "ai"


class AiConfigError(Exception):
    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


@dataclass(frozen=True)
class AiState:
    enabled: bool
    active_provider_id: int | None
    policy: FieldPolicy


@dataclass(frozen=True)
class Actor:
    id: int
    username: str
    scope: str | None = None


def _row(db: Session) -> SystemSetting | None:
    return db.scalar(select(SystemSetting).filter_by(key=SETTINGS_KEY))


def state(db: Session) -> AiState:
    """Read on every AI call: the switch acts the moment it is flipped."""
    row = _row(db)
    v = (row.value if row else None) or {}
    return AiState(enabled=bool(v.get("enabled", False)),
                   active_provider_id=v.get("active_provider_id"),
                   policy=FieldPolicy.from_dict(v.get("policy")))


def _save(db: Session, actor: Actor, action: str, value: dict) -> None:
    row = _row(db)
    before = dict(row.value) if row else None
    if row is None:
        row = SystemSetting(key=SETTINGS_KEY, value=value, created_by=actor.id,
                            description="AI module: master switch, active provider, "
                                        "data-leaving policy")
        db.add(row)
    else:
        row.value = value
        row.updated_by = actor.id
    db.flush()
    audit.record(db, action=action, entity_type="ai_settings", entity_id=SETTINGS_KEY,
                 before=before, after=value, actor_user_id=actor.id,
                 actor_username=actor.username, actor_scope=actor.scope)


def _value(s: AiState) -> dict:
    return {"enabled": s.enabled, "active_provider_id": s.active_provider_id,
            "policy": s.policy.to_dict()}


def ensure_row(db: Session) -> None:
    """Create the settings row, switched off, if it is missing (seed step)."""
    if _row(db) is None:
        db.add(SystemSetting(key=SETTINGS_KEY, value=_value(AiState(False, None, FieldPolicy())),
                             description="AI module: master switch, active provider, "
                                         "data-leaving policy"))
        db.flush()


def set_enabled(db: Session, actor: Actor, enabled: bool) -> AiState:
    s = state(db)
    if enabled and s.active_provider_id is None:
        raise AiConfigError("no_provider", "connect and activate a provider before "
                                           "turning AI on")
    new = AiState(enabled, s.active_provider_id, s.policy)
    _save(db, actor, "AI_ENABLED" if enabled else "AI_DISABLED", _value(new))
    return new


def set_policy(db: Session, actor: Actor, policy: dict) -> AiState:
    s = state(db)
    new = AiState(s.enabled, s.active_provider_id, FieldPolicy.from_dict(policy))
    _save(db, actor, "AI_POLICY_CHANGED", _value(new))
    return new


# --- providers --------------------------------------------------------------- #

def _public(p: AiProvider) -> dict:
    """Everything about a provider except the key."""
    return {
        "id": p.id, "brand": p.brand, "kind": p.kind, "label": p.label,
        "base_url": p.base_url, "model": p.model, "key_last4": p.key_last4,
        "has_key": p.api_key_enc is not None,
        "max_data_tier": Tier(p.max_data_tier).name, "is_free_tier": p.is_free_tier,
        "trial_ack_at": p.trial_ack_at.isoformat() if p.trial_ack_at else None,
        "status": p.status, "status_detail": p.status_detail,
        "last_tested_at": p.last_tested_at.isoformat() if p.last_tested_at else None,
        "daily_token_budget": p.daily_token_budget,
        # Whether the copilot chains lookups with this model, and whether that
        # was an administrator's choice or read from the model's name.
        "agentic": is_agentic(p),
        "agentic_override": p.agentic,
    }


def is_agentic(p: AiProvider) -> bool:
    return p.agentic if p.agentic is not None else agentic_default(p.kind, p.model)


public = _public


def list_providers(db: Session) -> list[AiProvider]:
    return list(db.scalars(select(AiProvider).order_by(AiProvider.id)))


def get_provider(db: Session, pid: int) -> AiProvider:
    p = db.get(AiProvider, pid)
    if p is None:
        raise AiConfigError("not_found", f"provider {pid} not found", 404)
    return p


def _check_tier(p: AiProvider, tier: Tier, trial_ack: bool, actor: Actor) -> None:
    """A free tier may train on prompts: clearing one for bank data is a
    decision someone has to take on the record."""
    local = PRESETS.get(p.brand) is not None and PRESETS[p.brand].local
    if tier > Tier.PUBLIC and p.is_free_tier and not local:
        if not trial_ack and p.trial_ack_at is None:
            raise AiConfigError(
                "trial_ack_required",
                "this is a free tier, whose terms may let the provider use prompts. "
                "Clearing it for bank data needs the trial acknowledgement.")
        if trial_ack and p.trial_ack_at is None:
            p.trial_ack_by, p.trial_ack_at = actor.id, datetime.now(timezone.utc)
    p.max_data_tier = int(tier)


def create_provider(db: Session, actor: Actor, *, brand: str, model: str,
                    api_key: str | None, base_url: str | None, label: str | None,
                    max_data_tier: str, is_free_tier: bool | None,
                    trial_ack: bool, daily_token_budget: int = 0) -> AiProvider:
    preset = PRESETS.get(brand)
    if preset is None:
        raise AiConfigError("bad_brand", f"unknown provider {brand!r}")
    try:
        url = validate_base_url(base_url or preset.base_url, production=core.is_production)
    except ValueError as exc:
        raise AiConfigError("bad_url", str(exc)) from exc
    if preset.needs_key and not api_key:
        raise AiConfigError("key_required", f"{preset.label} needs an API key")
    p = AiProvider(
        brand=brand, kind=preset.kind, label=label or preset.label, base_url=url,
        model=model.strip(), created_by=actor.id,
        is_free_tier=preset.free_tier if is_free_tier is None else is_free_tier,
        daily_token_budget=max(0, daily_token_budget),
    )
    if api_key:
        p.api_key_enc, p.key_last4 = crypto.encrypt(api_key.strip()), crypto.last4(api_key.strip())
    _check_tier(p, Tier[max_data_tier], trial_ack, actor)
    db.add(p)
    db.flush()
    audit.record(db, action="AI_PROVIDER_CREATED", entity_type="ai_provider",
                 entity_id=p.id, after=_public(p), actor_user_id=actor.id,
                 actor_username=actor.username, actor_scope=actor.scope)
    return p


def update_provider(db: Session, actor: Actor, pid: int, *, model: str | None = None,
                    api_key: str | None = None, label: str | None = None,
                    max_data_tier: str | None = None, is_free_tier: bool | None = None,
                    trial_ack: bool = False, daily_token_budget: int | None = None,
                    agentic: str | None = None) -> AiProvider:
    p = get_provider(db, pid)
    before = _public(p)
    if agentic is not None:
        # "auto" hands the decision back to the model's name.
        p.agentic = {"auto": None, "on": True, "off": False}[agentic]
    if model:
        p.model = model.strip()
    if label:
        p.label = label.strip()
    if api_key:
        p.api_key_enc, p.key_last4 = crypto.encrypt(api_key.strip()), crypto.last4(api_key.strip())
        p.status, p.status_detail = "untested", None
    if is_free_tier is not None:
        p.is_free_tier = is_free_tier
    if daily_token_budget is not None:
        p.daily_token_budget = max(0, daily_token_budget)
    _check_tier(p, Tier[max_data_tier] if max_data_tier else Tier(p.max_data_tier),
                trial_ack, actor)
    db.flush()
    after = _public(p)
    if api_key:
        after = {**after, "key": "replaced"}
    audit.record(db, action="AI_PROVIDER_UPDATED", entity_type="ai_provider", entity_id=p.id,
                 before=before, after=after, actor_user_id=actor.id,
                 actor_username=actor.username, actor_scope=actor.scope)
    return p


def delete_provider(db: Session, actor: Actor, pid: int) -> None:
    p = get_provider(db, pid)
    s = state(db)
    if s.active_provider_id == pid and s.enabled:
        raise AiConfigError("active", "turn AI off or activate another provider first", 409)
    before = _public(p)
    db.delete(p)
    if s.active_provider_id == pid:
        _save(db, actor, "AI_PROVIDER_DEACTIVATED",
              _value(AiState(False, None, s.policy)))
    audit.record(db, action="AI_PROVIDER_DELETED", entity_type="ai_provider", entity_id=pid,
                 before=before, actor_user_id=actor.id, actor_username=actor.username,
                 actor_scope=actor.scope)


def activate_provider(db: Session, actor: Actor, pid: int) -> AiState:
    p = get_provider(db, pid)
    if p.status != "ok":
        raise AiConfigError("untested", "test the connection successfully before activating")
    s = state(db)
    new = AiState(s.enabled, p.id, s.policy)
    _save(db, actor, "AI_PROVIDER_ACTIVATED", _value(new))
    return new


def record_test(db: Session, p: AiProvider, ok: bool, detail: str | None) -> None:
    """Connection status is operational state, not configuration: not audited."""
    p.status = "ok" if ok else "failed"
    p.status_detail = detail
    p.last_tested_at = datetime.now(timezone.utc)
    db.flush()

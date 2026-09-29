"""The single path by which anything reaches an AI provider.

    switch on? -> provider active? -> tier the payload -> provider cleared for
    that tier? -> DLP scan -> within budget? -> call -> log exactly what left
    -> ground the numbers -> re-hydrate tokens

Every call is logged to `ai_requests` -- allowed, blocked or failed -- in its
own transaction, committed before this function returns or raises. A blocked
request is still evidence: it shows the gate worked, and what it stopped.

Only this module constructs provider clients (import-linter contract).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timezone
from decimal import Decimal
from typing import Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai import crypto
from app.ai import settings_service as svc
from app.ai.config import ai_settings
from app.ai.gateway import dlp, grounding
from app.ai.gateway.policy import PolicyViolation, Tier, check_provider, payload_tier
from app.ai.gateway.tokenizer import Vault
from app.ai.models import AiProvider, AiRequest
from app.ai.providers.base import ChatMessage, ProviderError, best_model
from app.ai.providers.registry import build
from app.core.db import session_scope


# --- request / result -------------------------------------------------------- #

@dataclass(frozen=True, slots=True)
class Segment:
    #: instruction | public | bank | user
    kind: str
    text: str


@dataclass
class Turn:
    role: str                      # user | assistant
    segments: list[Segment]

    @property
    def text(self) -> str:
        return "\n\n".join(s.text for s in self.segments)


@dataclass
class GatewayRequest:
    purpose: str
    system: str
    turns: list[Turn]
    vault: Vault | None = None
    #: Numbers the answer may contain; grounding runs when this is given.
    facts: list[Decimal] | None = None
    #: Purely numeric identifiers (branch codes) for the DLP scan.
    numeric_codes: set[str] = field(default_factory=set)
    max_tokens: int = 1500
    json_mode: bool = False


@dataclass(frozen=True)
class GatewayResult:
    text: str                      # re-hydrated, for display
    masked_text: str               # as the provider returned it
    request_id: int
    tier: str
    provider: str
    model: str
    grounded: bool | None
    unverified: tuple[str, ...]
    #: The provider stopped at its length limit: the text may end mid-sentence.
    truncated: bool = False


class AiUnavailable(Exception):
    """AI is switched off, or no provider is active."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class GatewayBlocked(Exception):
    def __init__(self, code: str, message: str, request_id: int | None):
        super().__init__(message)
        self.code, self.message, self.request_id = code, message, request_id


class GatewayError(Exception):
    def __init__(self, code: str, message: str, request_id: int | None, retryable: bool):
        super().__init__(message)
        self.code, self.message = code, message
        self.request_id, self.retryable = request_id, retryable


@dataclass(frozen=True)
class Caller:
    user_id: int | None
    username: str | None


SYSTEM_CALLER = Caller(None, "system")


# --- logging ----------------------------------------------------------------- #

def _log(**row) -> int:
    """Write one egress record in its own transaction and return its id."""
    with session_scope() as s:
        rec = AiRequest(**row)
        s.add(rec)
        s.flush()
        return rec.id


def _payload(req: GatewayRequest) -> dict:
    return {
        "system": req.system,
        "messages": [{"role": t.role, "segments": [s.kind for s in t.segments],
                      "content": t.text} for t in req.turns],
        "max_tokens": req.max_tokens, "json_mode": req.json_mode,
    }


def _tokens_today(db: Session, provider_id: int) -> int:
    start = datetime.combine(datetime.now(timezone.utc).date(), dtime.min, tzinfo=timezone.utc)
    q = select(func.coalesce(func.sum(func.coalesce(AiRequest.tokens_in, 0)
                                      + func.coalesce(AiRequest.tokens_out, 0)), 0)
               ).where(AiRequest.provider_id == provider_id, AiRequest.created_at >= start)
    return int(db.scalar(q) or 0)


def _client(p: AiProvider, model: str | None = None):
    return build(brand=p.brand, kind=p.kind, base_url=p.base_url,
                 api_key=crypto.decrypt(p.api_key_enc), model=model or p.model,
                 timeout=ai_settings().AI_HTTP_TIMEOUT)


# --- the call ---------------------------------------------------------------- #

def call(db: Session, caller: Caller, req: GatewayRequest) -> GatewayResult:
    state = svc.state(db)
    if not state.enabled:
        raise AiUnavailable("ai_disabled", "AI features are switched off")
    if state.active_provider_id is None:
        raise AiUnavailable("no_provider", "no AI provider is active")
    p = db.get(AiProvider, state.active_provider_id)
    if p is None:
        raise AiUnavailable("no_provider", "the active AI provider no longer exists")

    kinds = {s.kind for t in req.turns for s in t.segments}
    tier = payload_tier(kinds, state.policy)
    payload = _payload(req)
    base = dict(user_id=caller.user_id, username=caller.username, purpose=req.purpose,
                provider_id=p.id, provider_label=p.label, model=p.model, tier=int(tier),
                masked_payload=payload)

    def blocked(code: str, message: str, logged_payload: dict | None = None) -> GatewayBlocked:
        row = {**base, "masked_payload": logged_payload or payload}
        rid = _log(**row, status="blocked", blocked_reason=f"{code}: {message}")
        return GatewayBlocked(code, message, rid)

    # 1. Is the provider cleared for this data?
    try:
        check_provider(tier, Tier(p.max_data_tier))
    except PolicyViolation as exc:
        raise blocked(exc.code, exc.message) from exc

    # 2. Does anything real remain in the text? Fail closed.
    segments = [("instruction", req.system)] + [(s.kind, s.text) for t in req.turns
                                                for s in t.segments]
    known = req.vault.raw_spellings() if req.vault else set()
    hits = dlp.scan(segments, known, numeric_known=req.numeric_codes)
    if hits:
        rules = sorted({h.rule for h in hits})
        # The log keeps what was stopped, but must not become a new copy of it.
        def red(kind: str, text: str) -> str:
            return dlp.redact(kind, text, known, numeric_known=req.numeric_codes)
        redacted = {**payload,
                    "system": red("instruction", req.system),
                    "messages": [{"role": t.role, "segments": [x.kind for x in t.segments],
                                  "content": "\n\n".join(red(x.kind, x.text) for x in t.segments)}
                                 for t in req.turns]}
        raise blocked("dlp", f"outbound text still contains {', '.join(rules)} "
                             f"({len(hits)} finding(s)); nothing was sent", redacted)

    # 3. Within today's budget?
    if p.daily_token_budget and _tokens_today(db, p.id) >= p.daily_token_budget:
        raise blocked("budget", f"daily token budget of {p.daily_token_budget:,} reached")

    # 4. Call.
    started = time.perf_counter()
    try:
        out = _client(p).complete(
            system=req.system,
            messages=[ChatMessage(t.role, t.text) for t in req.turns],
            max_tokens=req.max_tokens, json_mode=req.json_mode)
    except ProviderError as exc:
        rid = _log(**base, status="error", blocked_reason=f"{exc.code}: {exc.message}",
                   latency_ms=int((time.perf_counter() - started) * 1000))
        raise GatewayError(exc.code, exc.message, rid, exc.retryable) from exc
    latency = int((time.perf_counter() - started) * 1000)

    # 5. Ground the numbers, then re-hydrate for display.
    g = grounding.check(out.text, req.facts) if req.facts is not None else None
    rid = _log(**{**base, "model": out.model}, status="ok", masked_response=out.text,
               grounded=None if g is None else g.ok,
               unverified=None if g is None else list(g.unverified),
               tokens_in=out.tokens_in, tokens_out=out.tokens_out, latency_ms=latency)
    text = req.vault.rehydrate(out.text) if req.vault else out.text
    return GatewayResult(text=text, masked_text=out.text, request_id=rid, tier=tier.name,
                         provider=p.label, model=out.model,
                         grounded=None if g is None else g.ok,
                         unverified=() if g is None else g.unverified,
                         truncated=out.finish == "length")


# --- connection tests -------------------------------------------------------- #

_PING = "Reply with the single word OK."


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    models: list[str]
    reply: str | None
    error_code: str | None
    error: str | None
    latency_ms: int | None
    #: The model that was tested -- not the one asked for, when the provider
    #: no longer offers that one.
    model: str | None = None


def probe(*, caller: Caller, brand: str, kind: str, base_url: str, api_key: str | None,
          model: str | None, preferred: tuple[str, ...] = ()) -> ProbeResult:
    """Check a key before it is saved: list models, and ping one if named.

    The ping is a fixed public sentence, logged like any other egress.
    """
    models: list[str] = []
    try:
        client = build(brand=brand, kind=kind, base_url=base_url, api_key=api_key,
                       model=model or "", timeout=min(ai_settings().AI_HTTP_TIMEOUT, 30))
        try:
            models = client.list_models()
        except ProviderError as exc:
            # Some endpoints do not list models; that alone is not a failure
            # when a model was named and answers.
            if exc.code == "auth" or not model:
                raise
        # Providers retire models; a suggested default the key no longer lists
        # would fail the ping and make a good key look bad. Test one it offers.
        if models and model not in models:
            model = best_model(models, preferred) or model
            client = build(brand=brand, kind=kind, base_url=base_url, api_key=api_key,
                           model=model or "", timeout=min(ai_settings().AI_HTTP_TIMEOUT, 30))
        if not model:
            return ProbeResult(True, models, None, None, None, None)
        res = _ping(client, caller=caller, label=f"{brand} (probe)", provider_id=None,
                    model=model, models=models)
        return ProbeResult(res.ok, res.models, res.reply, res.error_code, res.error,
                           res.latency_ms, model)
    except ProviderError as exc:
        return ProbeResult(False, models, None, exc.code, exc.message, None)


def test_provider(db: Session, caller: Caller, p: AiProvider) -> ProbeResult:
    try:
        client = _client(p)
        try:
            models = client.list_models()
        except ProviderError as exc:
            if exc.code == "auth":
                raise
            models = []
        res = _ping(client, caller=caller, label=p.label, provider_id=p.id, model=p.model,
                    models=models)
    except (ProviderError, RuntimeError) as exc:
        code = getattr(exc, "code", "config")
        res = ProbeResult(False, [], None, code, str(getattr(exc, "message", exc)), None)
    svc.record_test(db, p, res.ok, None if res.ok else f"{res.error_code}: {res.error}")
    return res


def _ping(client, *, caller: Caller, label: str, provider_id: int | None, model: str,
          models: list[str]) -> ProbeResult:
    payload = {"system": "Connection test.", "messages": [
        {"role": "user", "segments": ["public"], "content": _PING}], "max_tokens": 16}
    started = time.perf_counter()
    try:
        out = client.complete(system="Connection test.",
                              messages=[ChatMessage("user", _PING)], max_tokens=16)
    except ProviderError as exc:
        _log(user_id=caller.user_id, username=caller.username, purpose="connection_test",
             provider_id=provider_id, provider_label=label, model=model, tier=0,
             masked_payload=payload, status="error",
             blocked_reason=f"{exc.code}: {exc.message}")
        raise
    latency = int((time.perf_counter() - started) * 1000)
    _log(user_id=caller.user_id, username=caller.username, purpose="connection_test",
         provider_id=provider_id, provider_label=label, model=out.model, tier=0,
         masked_payload=payload, status="ok", masked_response=out.text,
         tokens_in=out.tokens_in, tokens_out=out.tokens_out, latency_ms=latency)
    return ProbeResult(True, models, out.text.strip()[:200], None, None, latency)


def recent_requests(db: Session, *, limit: int = 50, offset: int = 0,
                    status: str | None = None, purpose: str | None = None
                    ) -> tuple[list[AiRequest], int]:
    q = select(AiRequest)
    c = select(func.count()).select_from(AiRequest)
    for col, val in ((AiRequest.status, status), (AiRequest.purpose, purpose)):
        if val:
            q, c = q.where(col == val), c.where(col == val)
    rows = list(db.scalars(q.order_by(AiRequest.id.desc()).limit(limit).offset(offset)))
    return rows, int(db.scalar(c) or 0)


def usage_today(db: Session, provider_ids: Iterable[int]) -> dict[int, int]:
    return {pid: _tokens_today(db, pid) for pid in provider_ids}

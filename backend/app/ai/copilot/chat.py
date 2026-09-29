"""Ask FTP: a question in, a table, a chart and a checked answer out.

    mask the question -> plan (model, sees no data) -> check the plan ->
    run it in the asker's scope (code) -> show table and chart ->
    narrate the masked result (model) -> ground every number -> re-hydrate

Two provider calls per question, both through the gateway and both in the
egress log. The table and chart come from the query, never from a model, and
reach the person before the narration does; if the narration fails, the
answer still stands.

`ask` yields events as they happen so the page can show progress.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterator

from sqlalchemy import delete, desc, func, select
from sqlalchemy.orm import Session

from app.ai import crypto
from app.ai import settings_service as svc
from app.ai.context.entities import org_entities
from app.ai.context.fact_sheet import names as org_names
from app.ai.copilot import tools
from app.ai.copilot.catalog import Plan, PlanError, parse_plan, planner_system
from app.ai.copilot.result import masked_text
from app.ai.gateway import gateway as gw
from app.ai.gateway.gateway import Caller, GatewayRequest, Segment, Turn
from app.ai.gateway.grounding import differences_by_line, numbers_in
from app.ai.gateway.policy import FieldPolicy
from app.ai.gateway.tokenizer import Entity, Vault
from app.ai.insights import engine
from app.ai.models import Conversation, Message, Pin
from app.core.db import session_scope
from app.domain.scope import ScopeFilter, ScopeViolation
from app.domain.types import ScopeLevel
from app.models import AggDailyBranch, Branch, District, Division, Product

MAX_PER_HOUR = 40
MAX_PINS = 12
LANGS = {"en": "English", "bn": "Bangla (বাংলা)"}


@dataclass(frozen=True)
class Asker:
    user_id: int
    username: str
    level: ScopeLevel
    scope_id: int | None
    scope: ScopeFilter

    @property
    def caller(self) -> Caller:
        return Caller(self.user_id, self.username)

    @property
    def reader(self) -> engine.Reader:
        return engine.Reader(self.user_id, self.level, self.scope_id)


# --- vault ------------------------------------------------------------------ #

def _vault(db: Session, conv: Conversation, policy: FieldPolicy) -> tuple[Vault, set[str]]:
    v = Vault.from_dict(json.loads(crypto.decrypt(conv.vault_enc) or "{}")) if conv.vault_enc \
        else Vault()
    ents, codes = org_entities(db, policy)
    v.register(ents)
    if policy.action("product_name") == "token":
        v.register(Entity("PRD", p.product_code, p.short_name, (p.short_name,))
                   for p in db.scalars(select(Product)))
    return v, codes


def _resolver(vault: Vault, policy: FieldPolicy, names: dict[str, str], div_codes: dict[str, str]):
    def resolve(kind: str, key: str) -> str:
        display = names.get(f"{kind}:{key}", key)
        if kind == "PRD":
            return f'"{display}"' if policy.action("product_name") == "pass" \
                else vault.token("PRD", key, display)
        if kind == "DIV":
            return vault.token("DIV", div_codes.get(str(key), f"id:{key}"), display)
        return vault.token(kind, key, display)
    return resolve


# --- the planner's context and the plan's filters ------------------------------ #

def _scope_words(db: Session, a: Asker, vault: Vault) -> str:
    if a.level is ScopeLevel.HO:
        return "the whole bank"
    if a.level is ScopeLevel.DIVISION:
        d = db.get(Division, a.scope_id)
        return f"one division, {vault.token('DIV', d.code)}" if d else "one division"
    if a.level is ScopeLevel.DISTRICT:
        t = db.get(District, a.scope_id)
        return f"one district, {vault.token('DIST', t.code)}" if t else "one district"
    b = db.get(Branch, a.scope_id)
    return f"one branch, {vault.token('BR', b.branch_code)}" if b else "one branch"


def _context(db: Session, a: Asker, vault: Vault, policy: FieldPolicy,
             history: list[Message]) -> str:
    q = select(func.min(AggDailyBranch.business_date), func.max(AggDailyBranch.business_date))
    if not a.scope.unrestricted:
        q = q.where(AggDailyBranch.branch_id.in_(a.scope.branch_ids or [-1]))
    lo, hi = db.execute(q).one()
    lines = [f"TODAY: {datetime.now(timezone.utc).date().isoformat()}",
             f"BANK DATA: {lo} to {hi} (latest business date {hi})" if hi else "BANK DATA: none yet",
             f"THE ASKER SEES: {_scope_words(db, a, vault)}. Queries are limited to this."]
    if policy.action("product_name") == "pass":
        prods = [f'"{p.short_name}"' for p in db.scalars(select(Product).where(Product.is_active.is_(True))
                                                         .order_by(Product.side, Product.short_name))]
        lines.append(f"PRODUCTS: {', '.join(prods)}")
    if history:
        lines.append("EARLIER IN THIS CONVERSATION (for follow-ups like 'now only deposits'):")
        for m in history[-3:]:
            lines.append(f"Q: {m.masked_question}")
            if m.plan:
                lines.append(f"PLAN: {json.dumps(_plan_for_context(m.plan), separators=(',', ':'))}")
    return "\n".join(lines)


def _plan_for_context(p: dict) -> dict:
    """An earlier plan, as the planner wrote it (tokens, not keys)."""
    keep = ("tool", "metrics", "by", "period", "compare", "division", "district", "branches",
            "products", "side", "category", "where", "sort", "order", "limit")
    return {k: p[k] for k in keep if p.get(k) not in (None, [], "", False)}


def resolve(db: Session, plan: Plan, vault: Vault) -> tools.Where:
    """The plan's tokens and names back to the platform's keys."""
    def key(tok: str, kind: str) -> str | None:
        e = vault.entity(tok.strip())
        if e is not None:
            if e.kind != kind:
                raise PlanError(f"{tok} is not a {kind.lower()}")
            return e.key
        return None

    codes = []
    for t in plan.branches:
        k = key(t, "BR") or db.scalar(select(Branch.branch_code).where(
            func.lower(Branch.branch_name) == t.lower().strip()))
        if not k:
            raise PlanError(f"no branch matches {t!r}")
        codes.append(k)
    div_id = None
    if plan.division:
        code = key(plan.division, "DIV")
        d = db.scalar(select(Division).where(Division.code == code)) if code else db.scalar(
            select(Division).where(func.lower(Division.name) == plan.division.lower()
                                   .removesuffix(" division").strip()))
        if d is None:
            raise PlanError(f"no division matches {plan.division!r}")
        div_id = d.id
    dist = None
    if plan.district:
        dist = key(plan.district, "DIST") or db.scalar(select(District.code).where(
            func.lower(District.name) == plan.district.lower().removesuffix(" district").strip()))
        if not dist:
            raise PlanError(f"no district matches {plan.district!r}")
    prods = []
    for t in plan.products:
        k = key(t, "PRD") if t.upper().startswith("PRD_") else None
        if k is None:
            name = t.strip().strip('"').lower()
            k = db.scalar(select(Product.product_code).where(
                (func.lower(Product.short_name) == name) | (func.lower(Product.product_code) == name)))
        if not k:
            raise PlanError(f"no product matches {t!r}")
        prods.append(k)
    return tools.Where(tuple(codes), div_id, dist, tuple(prods), plan.side, plan.category)


# --- prompts -------------------------------------------------------------------- #

def _narrator_system(lang: str, explain: bool) -> str:
    base = ("You are FTP Intelligence, answering a banker's question inside a Bangladeshi bank's "
            "Funds Transfer Pricing platform.\n")
    if explain:
        return base + ("Answer the concept question plainly in under 120 words, for a banker. "
                       "Do not state any figure about this bank; you have none. "
                       f"Write in {LANGS[lang]}.")
    return base + (
        "The platform ran a query; its table and chart are shown beside your answer. Explain "
        "the result.\nRules:\n"
        "- Use only numbers that appear in RESULT, written the same way (you may round a rate). "
        "Amounts are BDT crore: say 'crore'. Never compute new figures.\n"
        "- Tokens like BR_K7Q, DIV_2MX, DIST_4TA, PRD_9QX stand for names you are not shown. "
        "Copy them exactly; never guess what they are.\n"
        "- Lead with the direct answer in one sentence, then at most three short sentences or "
        "'- ' bullets on what stands out. No preamble, no advice the result does not support.\n"
        "- If nothing matched, say what was checked and that nothing matched.\n"
        f"- Under 120 words. Write in {LANGS[lang]}"
        + ("; keep tokens, product names and numbers exactly as given, in English digits."
           if lang == "bn" else "."))


# --- the question --------------------------------------------------------------- #

def _count_last_hour(db: Session, user_id: int) -> int:
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    return int(db.scalar(select(func.count()).select_from(Message).where(
        Message.user_id == user_id, Message.created_at >= since)) or 0)


def _gateway_error(exc: Exception) -> dict:
    if isinstance(exc, gw.AiUnavailable):
        return {"type": "error", "code": exc.code, "text": exc.message}
    if isinstance(exc, gw.GatewayBlocked):
        why = {"tier_exceeded": "The active AI provider is not cleared for the bank's data, so "
                                "questions cannot be sent to it. An administrator can clear it in "
                                "AI management.",
               "dlp": "The question still contained something that identifies the bank, so "
                      "nothing was sent.",
               "budget": "Today's AI budget has been used up."}.get(exc.code, exc.message)
        return {"type": "error", "code": f"blocked_{exc.code}", "text": why}
    if isinstance(exc, gw.GatewayError):
        return {"type": "error", "code": f"provider_{exc.code}",
                "text": f"The AI provider did not answer: {exc.message}"}
    raise exc


def ask(a: Asker, question: str, conversation_id: str | None, lang: str) -> Iterator[dict]:
    question = question.strip()
    with session_scope() as db:
        state = svc.state(db)
        if not state.enabled:
            yield {"type": "error", "code": "ai_disabled", "text": "AI features are switched off."}
            return
        if _count_last_hour(db, a.user_id) >= MAX_PER_HOUR:
            yield {"type": "error", "code": "rate_limited",
                   "text": f"That is {MAX_PER_HOUR} questions in the last hour; try again shortly."}
            return
        conv = db.get(Conversation, conversation_id) if conversation_id else None
        if conv is None or conv.user_id != a.user_id:
            conv = Conversation(id=str(uuid.uuid4()), user_id=a.user_id, title=question[:200])
            db.add(conv)
            db.flush()
        history = list(db.scalars(select(Message).where(Message.conversation_id == conv.id,
                                                        Message.status == "ok")
                                  .order_by(Message.id)))
        vault, codes = _vault(db, conv, state.policy)
        names = org_names(db)
        div_codes = {str(d.id): d.code for d in db.scalars(select(Division))}
        resolver = _resolver(vault, state.policy, names, div_codes)
        masked_q = vault.mask_text(question)
        msg = Message(conversation_id=conv.id, user_id=a.user_id, lang=lang, question=question,
                      masked_question=masked_q, status="error", request_ids=[])
        yield {"type": "start", "conversation_id": conv.id, "sent": masked_q}

        def finish() -> None:
            conv.vault_enc = crypto.encrypt(json.dumps(vault.to_dict()))
            conv.updated_at = datetime.now(timezone.utc)
            db.add(msg)
            db.flush()

        # 1. Plan.
        yield {"type": "status", "text": "Working out what to look up"}
        context = _context(db, a, vault, state.policy, history)
        turns = [Turn("user", [Segment("instruction", context), Segment("user", masked_q)])]
        plan: Plan | None = None
        for attempt in range(2):
            try:
                r = gw.call(db, a.caller, GatewayRequest(
                    purpose="ask_plan", system=planner_system(), turns=turns, vault=vault,
                    numeric_codes=codes, max_tokens=600, json_mode=True))
            except (gw.AiUnavailable, gw.GatewayBlocked, gw.GatewayError) as exc:
                ev = _gateway_error(exc)
                msg.answer = ev["text"]
                finish()
                yield ev
                yield {"type": "done", "message_id": msg.id, "pinnable": False}
                return
            msg.request_ids = [*(msg.request_ids or []), r.request_id]
            try:
                plan = parse_plan(r.masked_text)
                break
            except PlanError as exc:
                turns = turns + [Turn("assistant", [Segment("user", r.masked_text[:2000])]),
                                 Turn("user", [Segment("instruction",
                                                       f"That is not a valid plan: {exc}. Reply "
                                                       f"with the corrected JSON plan only.")])]
        if plan is None:
            msg.answer = "I could not turn that into a query. Try naming a measure and a period."
            finish()
            yield {"type": "error", "code": "no_plan", "text": msg.answer}
            yield {"type": "done", "message_id": msg.id, "pinnable": False}
            return
        msg.plan = plan.to_dict()

        if plan.tool == "clarify":
            msg.status, msg.answer = "clarify", vault.rehydrate(plan.message or
                                                                 "Could you say a little more?")
            finish()
            yield {"type": "clarify", "text": msg.answer}
            yield {"type": "done", "message_id": msg.id, "pinnable": False}
            return

        # 2. Run it, in the asker's scope.
        res = None
        data_text = ""
        if plan.tool != "explain":
            try:
                where = resolve(db, plan, vault)
            except PlanError as exc:
                msg.status, msg.answer = "refused", f"I could not match part of that: {exc}."
                finish()
                yield {"type": "refused", "text": msg.answer}
                yield {"type": "done", "message_id": msg.id, "pinnable": False}
                return
            msg.where = where.to_dict()
            yield {"type": "status", "text": "Running the query"}
            try:
                res = tools.execute(db, a.scope, plan, where, head_office=a.level is ScopeLevel.HO,
                                    reader=a.reader, names=names)
            except ScopeViolation:
                msg.status = "refused"
                msg.answer = ("That is outside the part of the bank you can see, so it was not "
                              "looked up.")
                finish()
                yield {"type": "refused", "text": msg.answer}
                yield {"type": "done", "message_id": msg.id, "pinnable": False}
                return
            except tools.ToolRefused as exc:
                msg.status, msg.answer = "refused", exc.message
                finish()
                yield {"type": "refused", "text": exc.message}
                yield {"type": "done", "message_id": msg.id, "pinnable": False}
                return
            msg.result = res.to_json(names)
            yield {"type": "result", "result": msg.result, "tool": plan.tool}
            data_text = masked_text(res, resolver)

        # 3. Narrate.
        yield {"type": "status", "text": "Writing the answer"}
        explain = plan.tool == "explain"
        segs = [Segment("user", masked_q)]
        if data_text:
            kind = "public" if plan.tool == "market" else "bank"
            segs.append(Segment(kind, f"RESULT:\n{data_text}"))
        if lang == "bn":
            # Smaller models follow the last instruction they read, not one
            # at the end of a long system prompt.
            segs.append(Segment("instruction", "উত্তরটি বাংলায় লিখুন (write the answer in Bangla "
                                               "script). Keep tokens, product names and numbers "
                                               "exactly as given."))
        facts = None if explain else (differences_by_line(data_text)
                                      + [v for _, v, _ in numbers_in(masked_q)])
        try:
            r = gw.call(db, a.caller, GatewayRequest(
                purpose="ask_answer", system=_narrator_system(lang, explain),
                turns=[Turn("user", segs)], vault=vault, facts=facts, numeric_codes=codes,
                max_tokens=500))
            msg.request_ids = [*(msg.request_ids or []), r.request_id]
            msg.answer, msg.grounded, msg.unverified = r.text, r.grounded, list(r.unverified)
            msg.provider, msg.model = r.provider, r.model
            answer = {"type": "answer", "text": r.text, "grounded": r.grounded,
                      "unverified": list(r.unverified), "provider": r.provider, "model": r.model,
                      "explain": explain, "sent": data_text}
        except (gw.AiUnavailable, gw.GatewayBlocked, gw.GatewayError) as exc:
            # The table and chart stand without the words.
            ev = _gateway_error(exc)
            msg.answer = None
            answer = {"type": "answer", "text": None, "grounded": None, "unverified": [],
                      "note": ev["text"], "explain": explain, "sent": data_text}
        msg.status = "ok"
        finish()
        yield answer
        yield {"type": "done", "message_id": msg.id,
               "pinnable": res is not None and plan.tool in ("compare", "trend", "why",
                                                               "market", "benchmarks")}


# --- threads and pins ------------------------------------------------------------ #

def conversations(db: Session, user_id: int, limit: int = 20) -> list[Conversation]:
    return list(db.scalars(select(Conversation).where(Conversation.user_id == user_id)
                           .order_by(desc(Conversation.updated_at)).limit(limit)))


def thread(db: Session, user_id: int, conversation_id: str) -> tuple[Conversation, list[Message]] | None:
    c = db.get(Conversation, conversation_id)
    if c is None or c.user_id != user_id:
        return None
    return c, list(db.scalars(select(Message).where(Message.conversation_id == c.id)
                              .order_by(Message.id)))


def delete_conversation(db: Session, user_id: int, conversation_id: str) -> bool:
    c = db.get(Conversation, conversation_id)
    if c is None or c.user_id != user_id:
        return False
    db.delete(c)
    return True


class PinError(Exception):
    pass


def pin(db: Session, user_id: int, message_id: int, title: str | None) -> Pin:
    m = db.get(Message, message_id)
    if m is None or m.user_id != user_id or m.status != "ok" or not m.plan \
            or m.where is None or m.plan.get("tool") not in ("compare", "trend", "why", "market",
                                                              "benchmarks"):
        raise PinError("that answer cannot be pinned")
    n = db.scalar(select(func.count()).select_from(Pin).where(Pin.user_id == user_id)) or 0
    if n >= MAX_PINS:
        raise PinError(f"you already have {MAX_PINS} pins; remove one first")
    p = Pin(user_id=user_id, question=m.question, plan=m.plan, where=m.where,
            # The plan's own title, or the question: a result's title can carry
            # dates, and a pin moves with the data.
            title=(title or (m.plan or {}).get("title") or m.question)[:200])
    db.add(p)
    db.flush()
    return p


def pins(db: Session, user_id: int) -> list[Pin]:
    return list(db.scalars(select(Pin).where(Pin.user_id == user_id).order_by(Pin.id)))


def run_pin(db: Session, a: Asker, p: Pin) -> dict:
    """Re-run a pinned plan now. No model is involved."""
    names = org_names(db)
    try:
        res = tools.execute(db, a.scope, parse_plan(p.plan), tools.Where.from_dict(p.where),
                            head_office=a.level is ScopeLevel.HO, reader=a.reader, names=names)
    except ScopeViolation:
        return {"error": "outside your scope"}
    except (tools.ToolRefused, PlanError) as exc:
        return {"error": getattr(exc, "message", str(exc))}
    return res.to_json(names)


def unpin(db: Session, user_id: int, pin_id: int) -> bool:
    return bool(db.execute(delete(Pin).where(Pin.id == pin_id, Pin.user_id == user_id)).rowcount)  # type: ignore[attr-defined]

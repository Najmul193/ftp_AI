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
import re
import uuid
from dataclasses import dataclass
from dataclasses import replace as dataclass_replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Iterator

from sqlalchemy import delete, desc, func, select
from sqlalchemy.orm import Session

from app.ai import crypto
from app.ai import settings_service as svc
from app.ai.context.entities import org_entities
from app.ai.context.fact_sheet import names as org_names
from app.ai.copilot import tools
from app.ai.copilot.catalog import PAGES, Plan, PlanError, parse_plan, planner_system
from app.ai.copilot.result import direction_conflicts, masked_text
from app.ai.gateway import gateway as gw
from app.ai.gateway.gateway import Caller, GatewayRequest, Segment, Turn
from app.ai.gateway.grounding import differences_by_line, numbers_in
from app.ai.gateway.policy import FieldPolicy
from app.ai.gateway.tokenizer import Entity, Vault
from app.ai.insights import engine
from app.ai.insights.facts import fill
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
    #: May run what-if scenarios (SCENARIO_RUN).
    can_scenario: bool = False

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

def page_facts(db: Session, a: "Asker", page: "PageContext | None", resolve) -> tuple[str, list[Segment]]:
    """What the asker's page shows: notes for the context, and bank figures as
    their own segment (so the gateway tiers them as bank data)."""
    if page is None or not page.page:
        return "", []
    from app.ai.copilot import page_facts as pf
    f = pf.build(db, page.page, scope=a.scope, reader=a.reader, label="", resolve=resolve,
                 scenario=page.scenario, market=page.market)
    return "\n".join(f.notes), ([Segment("bank", "\n".join(f.facts))] if f.facts else [])

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
             history: list[Message], page: PageContext | None = None, resolve=None) -> str:
    q = select(func.min(AggDailyBranch.business_date), func.max(AggDailyBranch.business_date))
    if not a.scope.unrestricted:
        q = q.where(AggDailyBranch.branch_id.in_(a.scope.branch_ids or [-1]))
    lo, hi = db.execute(q).one()
    lines = [f"TODAY: {datetime.now(timezone.utc).date().isoformat()}",
             f"BANK DATA: {lo} to {hi} (latest business date {hi})" if hi else "BANK DATA: none yet",
             f"THE ASKER SEES: {_scope_words(db, a, vault)}. Queries are limited to this."]
    if page is not None and page.page:
        lines.append(f"THE ASKER IS ON THE PAGE: {PAGES[page.page][0]} -- it shows "
                     f"{PAGES[page.page][1]}.")
        filt = fill(page.describe(), resolve) if resolve else page.describe()
        lines.append(f"THE PAGE IS FILTERED TO: {filt or 'nothing (the whole of what the asker sees)'}.")
        if page.page == "coach" and page.where.branch_codes and resolve:
            lines.append(f"THIS BRANCH (open on the page): {resolve('BR', page.where.branch_codes[0])}.")
        lines.append("Words like 'this', 'here', 'these', 'this branch', 'that forecast', 'this "
                     "scenario' mean what this page shows (see ON THE PAGE below, if given). "
                     "The page's filters apply unless the question names something else, so do "
                     "not repeat them in the plan.")
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


def resolve_scenario(db: Session, plan: Plan, vault: Vault) -> Plan:
    """A scenario's products named the way the model saw them -- a name, a
    token -- back to product codes, as `resolve` does for a plan's filters."""
    if plan.tool != "scenario" or not plan.scenario:
        return plan
    by_name = {p.short_name.lower(): p.product_code for p in db.scalars(select(Product))}
    codes = set(by_name.values())

    def code(k: str) -> str:
        k = str(k).strip().strip('"')
        if k in codes:
            return k
        e = vault.entity(k)
        if e is not None and e.kind == "PRD":
            return e.key
        hit = by_name.get(k.lower())
        if hit:
            return hit
        raise PlanError(f"no product matches {k!r}")

    sc = dict(plan.scenario)
    for field_ in ("product_rate_bp", "product_bench_bp"):
        if isinstance(sc.get(field_), dict):
            sc[field_] = {code(k): v for k, v in sc[field_].items()}
    return dataclass_replace(plan, scenario=sc)


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
        "Amounts are BDT, marked cr (crore), lakh or taka: keep the unit as given. Never compute new figures.\n"
        "- Tokens like BR_K7Q, DIV_2MX, DIST_4TA, PRD_9QX stand for names you are not shown. "
        "Copy them exactly; never guess what they are.\n"
        "- Lead with the direct answer in one sentence, then at most three short sentences or "
        "'- ' bullets on what stands out. No preamble, no advice the result does not support.\n"
        "- If nothing matched, say what was checked and that nothing matched.\n"
        "- The question may carry a premise (\"why did X lose deposits\"). If RESULT shows the "
        "opposite, or does not show it, say so first, plainly, before anything else.\n"
        "- Describe only what RESULT measures. If it measures something other than what was "
        "asked, say what it shows instead.\n"
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
        if exc.retryable:
            return {"type": "error", "code": f"provider_{exc.code}",
                    "text": "The AI provider is busy right now and did not answer, even after "
                            "retrying. This usually clears within a minute: ask again. "
                            f"(It said: {exc.message})"}
        return {"type": "error", "code": f"provider_{exc.code}",
                "text": f"The AI provider did not answer: {exc.message}"}
    raise exc


def ambiguous_names(question: str, vault: Vault) -> list[tuple[str, list[str]]]:
    """Names in the question that belong to more than one branch (or
    district), with no code alongside to say which: [(name, [displays])].

    "Dhaka Main" is two branches. Masking would pick one silently; asking is
    the only honest answer.
    """
    by_name: dict[str, dict[tuple[str, str], Entity]] = {}
    for e in vault.entities():
        for n in {e.display, *e.aliases}:
            if n and len(n) >= 3 and not n.isdigit():
                by_name.setdefault(n.lower(), {})[(e.kind, e.key)] = e
    out = []
    low = question.lower()
    # Longest names first, each claiming its text: "Dhaka" inside "Dhaka Main"
    # is part of the branch's name, not a mention of Dhaka district.
    for name in sorted(by_name, key=len, reverse=True):
        pat = re.compile(r"(?<![\w])" + re.escape(name) + r"(?![\w])")
        if not pat.search(low):
            continue
        ents = by_name[name]
        if len(ents) > 1 and not any(
                re.search(r"(?<![\w])" + re.escape(e.key) + r"(?![\w])", question)
                for e in ents.values()):
            out.append((name, sorted(e.display for e in ents.values())))
        low = pat.sub(lambda m: " " * len(m.group(0)), low)
    return out


@dataclass(frozen=True)
class PageContext:
    """The page a question was asked on and what it was filtered to."""
    page: str | None
    where: tools.Where
    date_from: date | None = None
    date_to: date | None = None
    #: The scenario lab's settings on screen.
    scenario: dict | None = None
    #: The market rate explorer's selection: book, peers, product, bank.
    market: dict | None = None

    @property
    def label(self) -> str:
        return PAGES[self.page][0] if self.page in PAGES else ""

    def describe(self) -> str:
        """The filters in words, entities as placeholders."""
        bits = [self.where.describe()] if self.where.describe() else []
        if self.date_from and self.date_to:
            bits.append(f"{self.date_from:%d %b}–{self.date_to:%d %b %Y}")
        return ", ".join(bits)


def _iso(v) -> date | None:
    try:
        return date.fromisoformat(str(v)) if v else None
    except ValueError:
        return None


def page_context(db: Session, ctx: dict | None) -> PageContext | None:
    if not ctx:
        return None
    page = ctx.get("page") if ctx.get("page") in PAGES else None
    f = ctx.get("filters") or {}
    w = where_from_filters(db, f)
    code = ctx.get("branch")
    if page == "coach" and code and db.scalar(select(Branch.id).where(Branch.branch_code == code)):
        w = dataclass_replace(w, branch_codes=(str(code),), division_id=None, district_code=None,
                              category=None)
    df, dt = _iso(f.get("date_from")), _iso(f.get("date_to"))
    sc = ctx.get("scenario") if page == "scenario" and isinstance(ctx.get("scenario"), dict) else None
    mk = ctx.get("market") if page == "market" and isinstance(ctx.get("market"), dict) else None
    return PageContext(page, w, df, dt, sc, mk) if (df and dt) else \
        PageContext(page, w, scenario=sc, market=mk)


def _peer_notes(db: Session, code: str, plan: Plan) -> list[str]:
    """On the coach page, "compared with its peers" needs the peers: their
    median and the branch's rank, from the coach. Rates only -- a median
    amount would be an exact figure -- and the peer group as a placeholder."""
    from app.ai.coach import service as coach_service
    from app.ai.coach.rules import METRICS as COACH_METRICS
    try:
        view, c, _ = coach_service.build(db, code)
    except coach_service.CoachError:
        return []
    b = db.scalar(select(Branch).where(Branch.branch_code == code))
    if b is None:
        return []
    if c.peer_scope == "district":
        d = db.get(District, b.district_id)
        group = f"{{DIST:{d.code}}}" if d else "its district"
    else:
        group = f"{{DIV:{b.division_id}}}" if b.division_id else "its division"
    wanted = [m for m in plan.metrics if m in COACH_METRICS] or \
        ["cost_of_deposits", "yield_on_advances", "ftp_yield"]
    out = []
    for m in view["metrics"]:
        if m["key"] not in wanted or m["unit"] != "pct" or m["district_median"] is None:
            continue
        rank = m["district_rank"]
        out.append(f"Peers ({c.peer_count} branches of {group}): median {m['label'].lower()} "
                   f"{Decimal(m['district_median']):.2f}%"
                   + (f"; this branch ranks {rank[0]} of {rank[1]}" if rank else "")
                   + (" (lower is better)" if not m["higher_is_better"] else "") + ".")
    return out


_PLACE = ("branch_codes", "division_id", "district_code", "category")
_WHAT = ("product_codes", "side")


def merge_where(asked: tools.Where, page: tools.Where) -> tuple[tools.Where, bool]:
    """The question's own filters, with the page's filling what it left open.

    By group, not field by field: a question that names a branch has chosen its
    place, and the page's division must not narrow it to nothing.
    """
    used = False
    out = asked
    for group in (_PLACE, _WHAT):
        if not any(getattr(asked, g) for g in group) and any(getattr(page, g) for g in group):
            out = dataclass_replace(out, **{g: getattr(page, g) for g in group})
            used = True
    return out, used


def where_from_filters(db: Session, f: dict) -> tools.Where:
    """A page's filter bar (ids, as the dashboards send them) as a plan's filters."""
    ids = [int(x) for x in (f.get("branch_id") or [])][:100]
    codes = tuple(db.scalars(select(Branch.branch_code).where(Branch.id.in_(ids)))) if ids else ()
    dist = db.scalar(select(District.code).where(District.id == int(f["district_id"]))) \
        if f.get("district_id") else None
    side = f.get("side") if f.get("side") in ("ASSET", "LIABILITY") else None
    cat = f.get("branch_category") if f.get("branch_category") in ("URBAN", "SEMI_URBAN", "RURAL") \
        else None
    return tools.Where(codes, int(f["division_id"]) if f.get("division_id") else None, dist,
                       tuple(str(p) for p in (f.get("product_code") or []))[:50], side, cat)


def preset_plan(preset: dict) -> Plan:
    """A plan a page built (a "Why?" button), with the page's dates if it has them."""
    raw = dict(preset.get("plan") or {})
    f = preset.get("filters") or {}
    if f.get("date_from") and f.get("date_to"):
        raw.update(period="custom", date_from=f["date_from"], date_to=f["date_to"])
    return parse_plan(raw)


def ask(a: Asker, question: str, conversation_id: str | None, lang: str,
        preset: dict | None = None, context: dict | None = None) -> Iterator[dict]:
    """Answer a question. A `preset` -- a plan and filters built by a page's
    "Why?" button -- skips the planning call: the question is already exact."""
    question = question.strip()
    with session_scope() as db:
        state = svc.state(db)
        if not state.enabled:
            yield {"type": "error", "code": "ai_disabled", "text": "AI features are switched off."}
            return
        if preset is None:
            from app.ai.copilot import agent     # it builds on this module
            multi = agent.use_agent(db)
        else:
            multi = False
    if multi:
        yield from agent.ask(a, question, conversation_id, lang, context)
        return
    with session_scope() as db:
        state = svc.state(db)
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
        doubtful = ambiguous_names(question, vault) if preset is None else []
        masked_q = vault.mask_text(question)
        msg = Message(conversation_id=conv.id, user_id=a.user_id, lang=lang, question=question,
                      masked_question=masked_q, status="error", request_ids=[])
        yield {"type": "start", "conversation_id": conv.id, "sent": masked_q}

        def finish() -> None:
            conv.vault_enc = crypto.encrypt(json.dumps(vault.to_dict()))
            conv.updated_at = datetime.now(timezone.utc)
            db.add(msg)
            db.flush()

        if doubtful:
            name, options = doubtful[0]
            msg.status = "clarify"
            msg.answer = (f"There are {len(options)} with that name: {' and '.join(options)}. "
                          f"Which one do you mean? Add the code, e.g. \"{options[-1]}\".")
            finish()
            yield {"type": "clarify", "text": msg.answer}
            yield {"type": "done", "message_id": msg.id, "pinnable": False}
            return

        # 1. Plan.
        plan: Plan | None = None
        preset_where: tools.Where | None = None
        if preset is not None:
            try:
                plan = preset_plan(preset)
                preset_where = where_from_filters(db, preset.get("filters") or {})
                # A "Why?" on cost of deposits means deposits, whatever the page
                # filter says about side -- unless the page already narrowed it.
                if plan.side and not preset_where.side:
                    preset_where = dataclass_replace(preset_where, side=plan.side)
            except (PlanError, ValueError, TypeError) as exc:
                msg.answer = f"That question could not be built: {exc}."
                finish()
                yield {"type": "error", "code": "bad_preset", "text": msg.answer}
                yield {"type": "done", "message_id": msg.id, "pinnable": False}
                return
        else:
            yield {"type": "status", "text": "Working out what to look up"}
        page = page_context(db, context)
        notes, facts_segs = page_facts(db, a, page, resolver) if preset is None else ("", [])
        planner_ctx = _context(db, a, vault, state.policy, history, page, resolver) \
            if preset is None else ""
        if notes:
            planner_ctx += "\n" + notes
        turns = [Turn("user", [Segment("instruction", planner_ctx), *facts_segs,
                               Segment("user", masked_q)])]
        for attempt in range(0 if preset is not None else 2):
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
        # The model titles its plan with tokens; the person reads names.
        plan = dataclass_replace(plan, title=vault.rehydrate(plan.title))
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
        page_note: str | None = None
        if plan.tool != "explain":
            try:
                where = preset_where if preset_where is not None else resolve(db, plan, vault)
                plan = resolve_scenario(db, plan, vault)
                if preset_where is None and page is not None and plan.tool in ("compare", "trend", "why"):
                    where, used = merge_where(where, page.where)
                    if page.date_from and page.date_to and not plan.period_set:
                        plan = dataclass_replace(plan, period="custom", date_from=page.date_from,
                                                 date_to=page.date_to)
                        used = True
                    if used:
                        page_note = f"Using the filters set on the {page.label} page: {page.describe()}."
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
                                    reader=a.reader, names=names, can_scenario=a.can_scenario)
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
            if page_note:
                res.notes.insert(0, page_note)
            if page is not None and page.page == "coach" and page.where.branch_codes \
                    and plan.tool in ("compare", "trend"):
                res.notes += _peer_notes(db, page.where.branch_codes[0], plan)
            msg.result = res.to_json(names)
            yield {"type": "result", "result": msg.result, "tool": plan.tool}
            data_text = masked_text(res, resolver)

        # 3. Narrate.
        yield {"type": "status", "text": "Writing the answer"}
        explain = plan.tool == "explain"
        # The narrator reads the page too: "this bank" is the one open on it.
        segs = [*facts_segs, *([Segment("instruction", notes)] if notes else []),
                Segment("user", masked_q)]
        if data_text:
            kind = "public" if plan.tool in ("market", "market_forecast", "policy_outlook") or \
                (plan.tool == "market_rates" and not plan.include_ours) else "bank"
            segs.append(Segment(kind, f"RESULT:\n{data_text}"))
        if not explain:
            segs.append(Segment("instruction", "Before you answer, check every claim in the "
                                               "question against RESULT. If RESULT contradicts a "
                                               "claim or does not show it, begin with \"The "
                                               "figures do not show that:\" and say what they "
                                               "do show."))
        if lang == "bn":
            # Smaller models follow the last instruction they read, not one
            # at the end of a long system prompt.
            segs.append(Segment("instruction", "উত্তরটি বাংলায় লিখুন (write the answer in Bangla "
                                               "script). Keep tokens, product names and numbers "
                                               "exactly as given."))
        facts = None if explain else (differences_by_line(data_text)
                                      + [v for _, v, _ in numbers_in(masked_q)]
                                      + [v for x in facts_segs for v in differences_by_line(x.text)])
        try:
            r = gw.call(db, a.caller, GatewayRequest(
                purpose="ask_answer", system=_narrator_system(lang, explain),
                turns=[Turn("user", segs)], vault=vault, facts=facts, numeric_codes=codes,
                max_tokens=500))
            msg.request_ids = [*(msg.request_ids or []), r.request_id]
            # Numbers can all be real and still be told the wrong way round.
            conflicts = direction_conflicts(r.text, plan, res.rows) if res is not None else []
            grounded = False if conflicts else r.grounded
            msg.answer, msg.grounded = r.text, grounded
            msg.unverified = list(r.unverified) + [f"direction: {c}" for c in conflicts]
            msg.provider, msg.model = r.provider, r.model
            answer = {"type": "answer", "text": r.text, "grounded": grounded,
                      "unverified": list(r.unverified), "provider": r.provider, "model": r.model,
                      "explain": explain, "sent": data_text, "truncated": r.truncated,
                      "conflicts": conflicts}
        except (gw.AiUnavailable, gw.GatewayBlocked, gw.GatewayError) as exc:
            # The table and chart stand without the words.
            ev = _gateway_error(exc)
            msg.answer = None
            answer = {"type": "answer", "text": None, "grounded": None, "unverified": [],
                      "note": ev["text"], "explain": explain, "sent": data_text}
        msg.status = "ok"
        finish()
        yield answer
        if plan.tool != "explain":
            from app.ai.copilot.agent import followups
            yield {"type": "followups", "items": followups([plan.tool], question)}
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

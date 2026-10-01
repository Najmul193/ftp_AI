"""Ask FTP, multi-step: the model chains lookups, then explains them together.

    mask the question -> repeat up to MAX_STEPS:
        the model writes ONE plan (it sees the question, the context, and the
        masked results so far) -> check it -> run it in the asker's scope ->
        show its table and chart at once
    -> narrate all the masked results together -> ground every number
    against all of them -> re-hydrate

The same catalogue and the same checks as the single-step copilot
(`chat.ask`): a step is an ordinary plan, so the model can do nothing here it
could not do there -- it can only do more of it, in order, and use what one
lookup found to choose the next ("profit fell" -> "by product" -> "the T-bill
move behind it" -> "what a cut would do"). Every step is its own gateway call
and its own line in the egress log.

Used when the active provider is marked multi-step (`settings_service.
is_agentic`); otherwise `chat.ask` answers from one lookup.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import replace as dataclass_replace
from datetime import datetime, timezone
from typing import Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai import crypto
from app.ai import settings_service as svc
from app.ai.context.fact_sheet import names as org_names
from app.ai.copilot import chat, tools
from app.ai.copilot.catalog import Plan, PlanError, parse_plan, planner_system
from app.ai.copilot.result import direction_conflicts, masked_text
from app.ai.gateway import gateway as gw
from app.ai.gateway.gateway import GatewayRequest, Segment, Turn
from app.ai.gateway.grounding import differences_by_line, numbers_in
from app.ai.models import AiProvider, Conversation, Message
from app.core.db import session_scope
from app.domain.scope import ScopeViolation
from app.domain.types import ScopeLevel
from app.models import Division

MAX_STEPS = 4
#: Tools whose output is public market data; the rest are the bank's.
PUBLIC_TOOLS = ("market", "market_forecast", "policy_outlook")
#: Tools a page's filters apply to.
PAGE_TOOLS = ("compare", "trend", "why", "forecast", "scenario")


def use_agent(db: Session) -> bool:
    s = svc.state(db)
    p = db.get(AiProvider, s.active_provider_id) if s.active_provider_id else None
    return bool(p is not None and svc.is_agentic(p))


def agent_system() -> str:
    return planner_system() + """

YOU MAY LOOK THINGS UP IN SEVERAL STEPS (at most 4 plans). After each plan you are shown its RESULT.
Each time, reply with ONE JSON object, either
  {"thought": "<= 20 words: what you will check next and why", "plan": { ...one plan as above... }}
or, once the results answer the question,
  {"thought": "<= 20 words", "done": true}

How to work:
- Start with the lookup that answers the question most directly; then add only what explains or tests it.
- Good chains: a fall in profit -> "why" by product -> the market move behind it ("market") -> where it heads ("forecast").
  "Should we raise term-deposit rates?" -> "peer_compare" -> "scenario" with those products' rates raised.
  "Will BB cut, and what would it do to us?" -> "policy_outlook" -> "scenario" with market_bp -50.
- Use what a RESULT shows (a product token, a branch token, a size of move) in the next plan.
- Never repeat a plan. Stop as soon as the question can be answered: one step is often enough.
- If a RESULT says REFUSED or shows nothing, try one other way or stop."""


def narrator_system(lang: str) -> str:
    return ("You are FTP Intelligence, a copilot inside a Bangladeshi bank's Funds Transfer Pricing "
            "platform. The platform ran several lookups; their tables are shown beside your answer. "
            "Answer the banker's question from them.\nRules:\n"
            "- Use only numbers that appear in the RESULTs, written the same way (you may round a "
            "rate). Amounts are BDT, marked cr (crore) or lakh: keep the unit as given. Never compute new figures.\n"
            "- Tokens like BR_K7Q, DIV_2MX, PRD_9QX stand for names you are not shown; copy them "
            "exactly.\n"
            "- Lead with the direct answer in one sentence. Then connect the results -- what drives "
            "what, what is likely next, what it would mean -- in at most four short '- ' bullets.\n"
            "- Forecasts are likely ranges, not promises: say 'likely' and give the range. A "
            "policy 'odds' split is a summary of signals, not a market price.\n"
            "- If the results contradict the question's premise, say so first.\n"
            "- End with one line starting 'Next:' naming the single most useful action the "
            "results support, if any.\n"
            f"- Under 170 words. Write in {chat.LANGS[lang]}"
            + ("; keep tokens, product names and numbers exactly as given, in English digits."
               if lang == "bn" else "."))


#: After a tool: questions worth asking next, each with the tool it would use,
#: so a follow-up never repeats a lookup that has just run.
FOLLOWUPS: dict[str, list[tuple[str, str]]] = {
    "compare": [("Where is this heading by month-end?", "forecast"),
                ("What if Bangladesh Bank raises 50 bp?", "scenario")],
    "trend": [("Where is this heading by month-end?", "forecast"), ("Why did it move?", "why")],
    "why": [("Where is profit heading by month-end?", "forecast"),
            ("Which branches drove it?", "compare")],
    "forecast": [("What would a 50 bp cut do to this?", "scenario"),
                 ("Are our deposit rates competitive?", "peer_compare")],
    "market_forecast": [("Which way is the policy rate leaning?", "policy_outlook"),
                        ("What if call money rises 100 bp?", "scenario")],
    "policy_outlook": [("What if Bangladesh Bank cuts 50 bp?", "scenario"),
                       ("Where are market rates heading in 90 days?", "market_forecast")],
    "scenario": [("Which branches' profit moves most?", "compare"),
                 ("Are our deposit rates competitive?", "peer_compare"),
                 ("Where is profit heading by month-end?", "forecast")],
    "peer_compare": [("What if we match the private banks on term deposits?", "scenario"),
                     ("Where are deposits heading by month-end?", "forecast")],
    "benchmarks": [("What if Bangladesh Bank cuts 50 bp?", "scenario"),
                   ("Which way is the policy rate leaning?", "policy_outlook")],
    "market": [("Where are market rates heading in 90 days?", "market_forecast"),
               ("Which way is the policy rate leaning?", "policy_outlook")],
}


def followups(tools_used: list[str], asked: str) -> list[str]:
    out: list[str] = []
    for t in reversed(tools_used):
        for q, tool in FOLLOWUPS.get(t, []):
            if tool in tools_used or q in out or q.lower() == asked.lower():
                continue
            out.append(q)
    return out[:3]


def _parse_step(raw: str) -> tuple[str, Plan | None, bool]:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise PlanError("no JSON object in the reply")
    try:
        d = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise PlanError(f"the reply is not valid JSON: {exc.msg}") from exc
    if not isinstance(d, dict):
        raise PlanError("the reply is not an object")
    thought = str(d.get("thought") or "")[:200]
    if d.get("done"):
        return thought, None, True
    plan = d.get("plan")
    if plan is None and d.get("tool"):
        plan = d                              # a bare plan, as the single-step prompt asks
    if not isinstance(plan, dict):
        raise PlanError('reply with {"thought": ..., "plan": {...}} or {"thought": ..., "done": true}')
    return thought, parse_plan(plan), False


def _key(p: Plan) -> str:
    d = p.to_dict()
    d.pop("title", None)
    return json.dumps(d, sort_keys=True, default=str)


def ask(a: chat.Asker, question: str, conversation_id: str | None, lang: str,
        context: dict | None = None) -> Iterator[dict]:
    question = question.strip()
    with session_scope() as db:
        state = svc.state(db)
        if chat._count_last_hour(db, a.user_id) >= chat.MAX_PER_HOUR:
            yield {"type": "error", "code": "rate_limited",
                   "text": f"That is {chat.MAX_PER_HOUR} questions in the last hour; try again shortly."}
            return
        conv = db.get(Conversation, conversation_id) if conversation_id else None
        if conv is None or conv.user_id != a.user_id:
            conv = Conversation(id=str(uuid.uuid4()), user_id=a.user_id, title=question[:200])
            db.add(conv)
            db.flush()
        history = list(db.scalars(select(Message).where(Message.conversation_id == conv.id,
                                                        Message.status == "ok")
                                  .order_by(Message.id)))
        vault, codes = chat._vault(db, conv, state.policy)
        names = org_names(db)
        div_codes = {str(d.id): d.code for d in db.scalars(select(Division))}
        resolver = chat._resolver(vault, state.policy, names, div_codes)
        doubtful = chat.ambiguous_names(question, vault)
        masked_q = vault.mask_text(question)
        msg = Message(conversation_id=conv.id, user_id=a.user_id, lang=lang, question=question,
                      masked_question=masked_q, status="error", request_ids=[])
        yield {"type": "start", "conversation_id": conv.id, "sent": masked_q, "mode": "agent"}

        def finish() -> None:
            conv.vault_enc = crypto.encrypt(json.dumps(vault.to_dict()))
            conv.updated_at = datetime.now(timezone.utc)
            db.add(msg)
            db.flush()

        def stop(kind: str, text: str, status: str = "error", code: str | None = None) -> Iterator[dict]:
            msg.status, msg.answer = status, text
            finish()
            ev = {"type": kind, "text": text}
            if code:
                ev["code"] = code
            yield ev
            yield {"type": "done", "message_id": msg.id, "pinnable": False}

        if doubtful:
            name, options = doubtful[0]
            yield from stop("clarify", f"There are {len(options)} with that name: "
                                       f"{' and '.join(options)}. Which one do you mean? Add the code, "
                                       f"e.g. \"{options[-1]}\".", "clarify")
            return

        page = chat.page_context(db, context)
        ctx = chat._context(db, a, vault, state.policy, history, page, resolver)
        system = agent_system()
        convo: list[Turn] = [Turn("user", [Segment("instruction", ctx), Segment("user", masked_q)])]
        steps: list[tuple[Plan, dict | None, str]] = []      # plan, result json, masked text
        wheres: list[dict] = []
        seen: set[str] = set()
        explain = False

        for n in range(MAX_STEPS):
            yield {"type": "status", "text": "Deciding what to look up" if n == 0
                   else "Deciding whether to look further"}
            plan = thought = None
            done = False
            turns = convo
            for attempt in range(2):
                try:
                    r = gw.call(db, a.caller, GatewayRequest(
                        purpose="ask_agent_step", system=system, turns=turns, vault=vault,
                        numeric_codes=codes, max_tokens=700, json_mode=True))
                except (gw.AiUnavailable, gw.GatewayBlocked, gw.GatewayError) as exc:
                    if steps:
                        done = True             # answer from what there is
                        break
                    ev = chat._gateway_error(exc)
                    yield from stop("error", ev["text"], code=ev.get("code"))
                    return
                msg.request_ids = [*(msg.request_ids or []), r.request_id]
                try:
                    thought, plan, done = _parse_step(r.masked_text)
                    convo = convo + [Turn("assistant", [Segment("user", r.masked_text[:2000])])]
                    break
                except PlanError as exc:
                    turns = turns + [Turn("assistant", [Segment("user", r.masked_text[:2000])]),
                                     Turn("user", [Segment("instruction",
                                                           f"Not valid: {exc}. Reply with the "
                                                           f"corrected JSON only.")])]
            if done or plan is None:
                if plan is None and not done and not steps:
                    yield from stop("error", "I could not turn that into a lookup. Try naming a "
                                             "measure and a period.", code="no_plan")
                    return
                break
            if plan.tool == "clarify":
                if not steps:
                    yield from stop("clarify", vault.rehydrate(plan.message or
                                                               "Could you say a little more?"),
                                    "clarify")
                    return
                break
            if plan.tool == "explain":
                explain = not steps
                break
            k = _key(plan)
            if k in seen:
                break
            seen.add(k)
            plan = dataclass_replace(plan, title=vault.rehydrate(plan.title))
            yield {"type": "step", "n": n + 1, "tool": plan.tool, "title": plan.title,
                   "thought": vault.rehydrate(thought or "")}

            refused = None
            try:
                where = chat.resolve(db, plan, vault)
                plan = chat.resolve_scenario(db, plan, vault)
                if page is not None and plan.tool in PAGE_TOOLS:
                    where, _ = chat.merge_where(where, page.where)
                    if page.date_from and page.date_to and not plan.period_set and \
                            plan.tool in ("compare", "trend", "why"):
                        plan = dataclass_replace(plan, period="custom", date_from=page.date_from,
                                                 date_to=page.date_to)
                res = tools.execute(db, a.scope, plan, where, head_office=a.level is ScopeLevel.HO,
                                    reader=a.reader, names=names, can_scenario=a.can_scenario)
            except ScopeViolation:
                refused = "That is outside the part of the bank you can see."
            except tools.ToolRefused as exc:
                refused = exc.message
            except PlanError as exc:
                refused = f"Could not match part of that: {exc}."
            if refused:
                yield {"type": "step_refused", "n": n + 1, "text": refused}
                steps.append((plan, None, f"REFUSED: {refused}"))
                convo = convo + [Turn("user", [Segment("instruction", f"RESULT {n + 1}: REFUSED: "
                                                                      f"{refused}")])]
                continue
            rj = res.to_json(names)
            data = masked_text(res, resolver)
            wheres.append(where.to_dict())
            steps.append((plan, rj, data))
            yield {"type": "result", "result": rj, "tool": plan.tool, "step": n + 1}
            kind = "public" if plan.tool in PUBLIC_TOOLS else "bank"
            convo = convo + [Turn("user", [Segment(kind, f"RESULT {n + 1}:\n{data}"),
                                           Segment("instruction", "Next plan, or done?")])]

        ran = [(p, rj, t) for p, rj, t in steps if rj is not None]
        # The first plan stands as the message's plan, so a one-step answer
        # pins like any other; the steps are kept beside it.
        msg.plan = {**(ran[0][0].to_dict() if ran else {}), "agent": True,
                    "steps": [p.to_dict() for p, _, _ in steps]}
        if wheres:
            msg.where = wheres[0]
        if ran:
            first = ran[0][1]
            msg.result = {**first, "steps": [{"tool": p.tool, "result": rj} for p, rj, _ in ran]}
        if not ran and not explain:
            reason = next((t for _, rj, t in steps if rj is None), "Nothing could be looked up.")
            yield from stop("refused", reason.removeprefix("REFUSED: "), "refused")
            return

        # Narrate everything together.
        yield {"type": "status", "text": "Writing the answer"}
        segs = [Segment("user", masked_q)]
        for i, (p, _, t) in enumerate(ran, start=1):
            kind = "public" if p.tool in PUBLIC_TOOLS else "bank"
            segs.append(Segment(kind, f"RESULT {i} ({p.tool}):\n{t}"))
        for m in history[-2:]:
            if m.answer:
                segs.insert(0, Segment("bank", f"EARLIER: Q: {m.masked_question}\n"
                                               f"A: {vault.mask_text(m.answer)[:600]}"))
        if lang == "bn":
            segs.append(Segment("instruction", "উত্তরটি বাংলায় লিখুন (write the answer in Bangla "
                                               "script). Keep tokens, product names and numbers "
                                               "exactly as given."))
        facts = None
        if not explain:
            facts = [v for _, v, _ in numbers_in(masked_q)]
            for _, _, t in ran:
                facts += differences_by_line(t)
        try:
            r = gw.call(db, a.caller, GatewayRequest(
                purpose="ask_answer", system=chat._narrator_system(lang, True) if explain
                else narrator_system(lang), turns=[Turn("user", segs)], vault=vault, facts=facts,
                numeric_codes=codes, max_tokens=700))
            msg.request_ids = [*(msg.request_ids or []), r.request_id]
            conflicts = []
            for p, rj, _ in ran:
                res_rows = rj.get("rows", []) if rj else []
                conflicts += direction_conflicts(r.text, p, res_rows)
            grounded = False if conflicts else r.grounded
            msg.answer, msg.grounded = r.text, grounded
            msg.unverified = list(r.unverified) + [f"direction: {c}" for c in conflicts]
            msg.provider, msg.model = r.provider, r.model
            answer = {"type": "answer", "text": r.text, "grounded": grounded,
                      "unverified": list(r.unverified), "provider": r.provider, "model": r.model,
                      "explain": explain, "sent": "\n\n".join(t for _, _, t in ran),
                      "truncated": r.truncated, "conflicts": conflicts, "steps": len(ran)}
        except (gw.AiUnavailable, gw.GatewayBlocked, gw.GatewayError) as exc:
            ev = chat._gateway_error(exc)
            msg.answer = None
            answer = {"type": "answer", "text": None, "grounded": None, "unverified": [],
                      "note": ev["text"], "explain": explain, "sent": ""}
        msg.status = "ok"
        finish()
        yield answer
        yield {"type": "followups", "items": followups([p.tool for p, _, _ in ran], question)}
        yield {"type": "done", "message_id": msg.id,
               "pinnable": len(ran) == 1 and ran[0][0].tool in ("compare", "trend", "why", "market",
                                                                 "benchmarks")}

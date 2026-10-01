"""The ALCO pack: what an asset-liability committee reads once a month, in one
printable page -- assembled by code, with an optional commentary written by
the model from the masked figures and checked against them.

    the policy outlook, market rates 90 days on, where the book lands,
    NII sensitivity to rate moves, our pricing against other banks, FTP
    benchmarks against the curve, and the open decisions.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai import settings_service as svc
from app.ai.context.entities import org_entities
from app.ai.context.fact_sheet import names as org_names
from app.ai.copilot import tools, tools_intel
from app.ai.copilot.catalog import Plan
from app.ai.copilot.result import masked_text
from app.ai.forecast import service as forecast
from app.ai.gateway import gateway as gw
from app.ai.gateway.gateway import Caller, GatewayRequest, Segment, Turn
from app.ai.gateway.generalize import crore
from app.ai.gateway.grounding import differences_by_line
from app.ai.gateway.tokenizer import Entity, Vault
from app.ai.insights import engine
from app.ai.insights.facts import fill_names
from app.ai.market import service as market
from app.ai.public import service as public
from app.ai.scenario import engine as scenario_engine
from app.ai.scenario import service as scenario
from app.domain.scope import ScopeFilter
from app.models import Division, Product

#: The rate moves every pack tests, with the bank's default pass-through.
SENSITIVITY = (
    ("Rates -100 bp", {"market_bp": -100}),
    ("Rates -50 bp", {"market_bp": -50}),
    ("Rates +50 bp", {"market_bp": 50}),
    ("Rates +100 bp", {"market_bp": 100}),
    ("Liquidity squeeze (+150 bp, slow deposit repricing)",
     {"market_bp": 150, "deposit_pass": 0.3, "demand_pass": 0.1, "loan_pass": 0.5,
      "competitor_bp": 50, "horizon_months": 1}),
    ("Deposits fall 5%", {"deposit_growth_pct": -5}),
)


def sensitivity(db: Session, scope: ScopeFilter) -> list[dict]:
    out = []
    for label, sc in SENSITIVITY:
        r = scenario.run(db, scope, scenario_engine.parse(sc))
        if not r.get("available"):
            return []
        c = r["change"]
        out.append({"label": label, "scenario": r["scenario"], "bank_nii": c["bank_nii"],
                    "branch_ftp": c["branch_ftp"], "treasury": c["treasury"],
                    "deposits": c["deposits"], "nim": c["nim"]})
    return out


def build(db: Session, scope: ScopeFilter, reader: engine.Reader, *, head_office: bool,
          label: str) -> dict:
    names = org_names(db)
    book = forecast.book_outlook(db, forecast.BookScope(scope, label=label))
    feed = [i for i, _ in engine.feed(db, reader, limit=40)
            if i.severity in ("critical", "serious", "warning")]
    bench = market.benchmarks(db, market.taka_curve(market.latest_observations(db)),
                              include_balances=head_office)["items"] if head_office else []
    return {
        "label": label, "prepared": date.today(),
        "policy": forecast.policy_outlook(db),
        "market": forecast.market_outlook(db)["series"],
        "book": book if book.get("available") else None,
        "sensitivity": sensitivity(db, scope),
        "pricing": public.book_vs_peers(db, branch_ids=None if scope.unrestricted
                                        else list(scope.branch_ids or [])),
        "benchmarks": [b for b in bench if b["gap_bp"] is not None],
        "decisions": [{"id": i.id, "title": fill_names(i.title, names), "severity": i.severity,
                       "money": i.money_at_stake, "basis": i.money_basis} for i in feed[:8]],
        "macro": public.macro(db),
    }


# --- the commentary ------------------------------------------------------------------ #

SYSTEM = (
    "You are the treasury analyst writing the commentary for a Bangladeshi bank's monthly ALCO "
    "(asset-liability committee) pack. The platform computed the figures below.\nRules:\n"
    "- Use only numbers that appear in the FIGURES, written the same way. Amounts are marked cr "
    "(crore), lakh or taka: keep the unit. Never compute new figures.\n"
    "- Tokens like PRD_9QX stand for product names you are not shown; copy them exactly.\n"
    "- Four short sections with these headings in bold: **Rates and policy**, **Our book**, "
    "**Sensitivity**, **For decision**. Two or three sentences each.\n"
    "- Forecasts are likely ranges, not promises. The policy odds summarise signals; they are not "
    "a market price.\n"
    "- Under 260 words, plain English, no preamble.")


def _vault(db: Session) -> tuple[Vault, set[str], callable]:
    state = svc.state(db)
    v = Vault()
    ents, codes = org_entities(db, state.policy)
    v.register(ents)
    if state.policy.action("product_name") == "token":
        v.register(Entity("PRD", p.product_code, p.short_name, (p.short_name,))
                   for p in db.scalars(select(Product)))
    names = org_names(db)
    div_codes = {str(d.id): d.code for d in db.scalars(select(Division))}

    def resolve(kind: str, key: str) -> str:
        display = names.get(f"{kind}:{key}", key)
        if kind == "PRD":
            return f'"{display}"' if state.policy.action("product_name") == "pass" \
                else v.token("PRD", key, display)
        if kind == "DIV":
            return v.token("DIV", div_codes.get(str(key), f"id:{key}"), display)
        return v.token(kind, key, display)
    return v, codes, resolve


def commentary(db: Session, caller: Caller, scope: ScopeFilter) -> dict:
    vault, codes, resolve = _vault(db)
    parts: list[tuple[str, str]] = []
    where = tools.Where()

    def add(kind: str, title: str, res) -> None:
        parts.append((kind, f"{title}:\n{masked_text(res, resolve, max_rows=12)}"))

    add("public", "POLICY OUTLOOK", tools_intel.policy_tool(db, Plan(tool="policy_outlook")))
    add("public", "MARKET RATES, 90 DAYS ON", tools_intel.market_forecast_tool(
        db, Plan(tool="market_forecast", codes=("BB_CALL_ON", "BB_TBILL_91", "BD_IND_DEPOSIT",
                                                 "US_FEDFUNDS", "FX_USDBDT"))))
    try:
        add("bank", "OUR BOOK, FORECAST", tools_intel.forecast_tool(
            db, scope, Plan(tool="forecast", metrics=("net_ftp_profit", "deposits", "advances",
                                                      "nim")), where))
    except tools.ToolRefused:
        pass
    lines = ["Change a month under each move, with the bank's usual pass-through:"]
    for row in sensitivity(db, scope):
        lines.append(f"- {row['label']}: bank NII {crore(row['bank_nii'])}; branches' FTP profit "
                     f"{crore(row['branch_ftp'])}; treasury {crore(row['treasury'])}; deposits "
                     f"{crore(row['deposits'])}")
    parts.append(("bank", "SENSITIVITY:\n" + "\n".join(lines)))
    try:
        add("bank", "OUR PRICING AGAINST OTHER BANKS",
            tools_intel.peer_tool(db, scope, Plan(tool="peer_compare")))
    except tools.ToolRefused:
        pass
    text_parts = [t for _, t in parts]
    facts: list[Decimal] = []
    for t in text_parts:
        facts += differences_by_line(t)
    segs = [Segment(kind, t) for kind, t in parts]
    segs.append(Segment("instruction", "Write the ALCO commentary from these FIGURES."))
    r = gw.call(db, caller, GatewayRequest(purpose="alco_commentary", system=SYSTEM,
                                           turns=[Turn("user", segs)], vault=vault, facts=facts,
                                           numeric_codes=codes, max_tokens=900))
    return {"text": r.text, "grounded": r.grounded, "unverified": list(r.unverified),
            "provider": r.provider, "model": r.model, "truncated": r.truncated,
            "sent": "\n\n".join(text_parts)}

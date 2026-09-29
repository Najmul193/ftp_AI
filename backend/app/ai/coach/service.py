"""Branch coach: build every branch's week, then coach one branch.

Peer figures are computed bank-wide on the server, because a branch's peers
are, by definition, outside a branch user's scope. What reaches that user is
only their own branch's figures, their district's *median* and their rank --
never another branch's name or numbers. The API checks the asker may see the
branch being coached.

Reads the aggregate tables only (one rollup per week, one for the deposit mix).
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai import settings_service as svc
from app.ai.coach.rules import METRICS, BranchStats, Coaching, coach
from app.ai.context.entities import user_vault
from app.ai.copilot.result import compute
from app.ai.copilot.tools import rollup
from app.ai.gateway import gateway as gw
from app.ai.gateway.gateway import Caller, GatewayRequest, Segment, Turn
from app.ai.gateway.generalize import crore, rate
from app.ai.gateway.grounding import differences_by_line
from app.domain.scope import ScopeFilter
from app.domain.types import LiabilityNature, Side
from app.models import AggDailyBranchProduct, Branch, District, Division, Product
from app.repositories.analytics import AnalyticsRepo
from app.repositories.dashboard import Filters

WINDOW = 7


class CoachError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def coachable(db: Session, scope: ScopeFilter) -> list[dict]:
    q = select(Branch, District.name).join(District, District.id == Branch.district_id) \
        .where(Branch.is_active.is_(True)).order_by(District.name, Branch.branch_name)
    if not scope.unrestricted:
        q = q.where(Branch.id.in_(scope.branch_ids or [-1]))
    return [{"code": b.branch_code, "name": b.branch_name, "district": d} for b, d in db.execute(q)]


def _mix(db: Session, start: date, end: date) -> dict[str, dict]:
    """Per branch: CASA share, and what CASA and term deposits each cost."""
    m = AggDailyBranchProduct
    rows = db.execute(
        select(m.branch_code, Product.liability_nature,
               func.sum(m.liability_balance), func.sum(m.interest_payable),
               func.sum(m.liability_balance / m.day_divisor))
        .join(Product, Product.id == m.product_id)
        .where(m.side == Side.LIABILITY, m.business_date.between(start, end))
        .group_by(m.branch_code, Product.liability_nature)).all()
    out: dict[str, dict] = {}
    for code, nature, bal, paid, bal_pd in rows:
        d = out.setdefault(code, {})
        key = "casa" if nature is LiabilityNature.DEMAND else "term"
        d[f"{key}_bal"] = Decimal(bal or 0)
        d[f"{key}_cost"] = (Decimal(paid or 0) / Decimal(bal_pd)).quantize(Decimal("0.0001")) \
            if bal_pd else None
    for d in out.values():
        total = d.get("casa_bal", Decimal(0)) + d.get("term_bal", Decimal(0))
        d["share"] = (d.get("casa_bal", Decimal(0)) / total * 100).quantize(Decimal("0.01")) \
            if total else None
    return out


def _week(a: AnalyticsRepo, start: date, end: date) -> dict[str, dict]:
    f = Filters(date_from=start, date_to=end)
    days = len(rollup(a, f, "date"))
    return {code: compute(meas, days) for code, meas in rollup(a, f, "branch").items()}


def everyone(db: Session) -> tuple[list[BranchStats], dict]:
    a = AnalyticsRepo(db, ScopeFilter(unrestricted=True))
    latest = a.dash.latest_business_date()
    if latest is None:
        raise CoachError("no_data", "No bank data has been loaded yet.")
    start = latest - timedelta(days=WINDOW - 1)
    pend = start - timedelta(days=1)
    pstart = pend - timedelta(days=WINDOW - 1)
    now, was = _week(a, start, latest), _week(a, pstart, pend)
    mix, mix_was = _mix(db, start, latest), _mix(db, pstart, pend)
    info = {b.branch_code: (b.district_id, b.division_id) for b in db.scalars(select(Branch))}
    stats = []
    for code, v in now.items():
        dist, div = info.get(code, (None, None))
        m = mix.get(code, {})
        stats.append(BranchStats(
            code=code, district=str(dist), division=div,
            now={**{k: v.get(k) for k in METRICS if k != "casa_share"}, "casa_share": m.get("share")},
            before={**{k: (was.get(code) or {}).get(k) for k in METRICS if k != "casa_share"},
                    "casa_share": mix_was.get(code, {}).get("share")},
            casa_cost=m.get("casa_cost"), term_cost=m.get("term_cost")))
    return stats, {"start": start, "end": latest, "prior_start": pstart, "prior_end": pend}


_UNITS = {"net_ftp_profit": "bdt", "deposits": "bdt", "advances": "bdt", "casa_share": "share"}


def build(db: Session, code: str) -> tuple[dict, Coaching, list[BranchStats]]:
    stats, period = everyone(db)
    me = next((s for s in stats if s.code == code), None)
    b = db.scalar(select(Branch).where(Branch.branch_code == code))
    if me is None or b is None:
        raise CoachError("no_data", "This branch has no data in the latest week.")
    c = coach(me, stats)
    dist = db.get(District, b.district_id)
    div = db.get(Division, b.division_id) if b.division_id else None
    peer_name = (f"{dist.name} district" if c.peer_scope == "district" and dist
                 else f"{div.name} division" if div else "the bank")
    metrics = []
    for key, (label, higher) in METRICS.items():
        v, med = me.now.get(key), c.district_median.get(key)
        metrics.append({
            "key": key, "label": label, "unit": _UNITS.get(key, "pct"), "higher_is_better": higher,
            "value": v, "before": me.before.get(key), "district_median": med,
            "bank_median": c.bank_median.get(key),
            "district_rank": c.district_rank.get(key), "bank_rank": c.rank.get(key),
            "better": None if v is None or med is None else (v >= med if higher else v <= med),
        })
    view = {
        "branch": {"code": b.branch_code, "name": b.branch_name,
                   "district": dist.name if dist else None},
        "peers": {"scope": c.peer_scope, "name": peer_name, "count": c.peer_count},
        "period": period,
        "rank": {"profit": c.rank.get("net_ftp_profit"),
                 "profit_before": c.rank_before.get("net_ftp_profit"),
                 "yield": c.rank.get("ftp_yield")},
        "metrics": metrics,
        "actions": [a.__dict__ for a in c.actions],
        "strengths": c.strengths,
    }
    return view, c, stats


def _masked(view: dict, c: Coaching, token_branch: str, token_peers: str) -> str:
    p = view["period"]
    lines = [f"BRANCH: {token_branch}, compared with the {view['peers']['count']} branches of "
             f"{token_peers} (its peer group).",
             f"WEEK: {p['start']} to {p['end']}, compared with {p['prior_start']} to {p['prior_end']}.",
             "Amounts in BDT crore (3 significant figures); rates in % a year."]
    r = view["rank"]
    if r["profit"]:
        lines.append(f"- FTP profit rank: {r['profit'][0]} of {r['profit'][1]} branches"
                     + (f" (the week before: {r['profit_before']})" if r["profit_before"] else ""))
    for m in view["metrics"]:
        v = m["value"]
        if v is None:
            continue
        fmt = (lambda x: crore(x)) if m["unit"] == "bdt" else \
              (lambda x: f"{Decimal(x):.1f}%") if m["unit"] == "share" else (lambda x: rate(x))
        line = f"- {m['label']}: {fmt(v)}"
        if m["district_median"] is not None:
            line += f"; peer median {fmt(m['district_median'])}"
        if m["district_rank"]:
            line += f"; rank {m['district_rank'][0]} of {m['district_rank'][1]} among peers"
        lines.append(line)
    if c.actions:
        lines.append("ACTIONS (ranked by the system by money at stake):")
        for i, a in enumerate(c.actions, 1):
            lines.append(f"{i}. {a.title}" + (f"; worth {crore(a.money)} {a.basis}" if a.money else ""))
    if c.strengths:
        lines.append("GOING WELL: " + " ".join(c.strengths))
    return "\n".join(lines)


_SYSTEM = (
    "You are FTP Intelligence, coaching a branch manager of a Bangladeshi bank on their "
    "branch's funds-transfer-pricing results. Be warm, direct and practical.\n"
    "Rules:\n- Use only numbers from the facts, written the same way; amounts are BDT crore.\n"
    "- Tokens like BR_K7Q and DIST_4TA stand for names you are not shown: copy them exactly.\n"
    "- Start with where the branch stands in one sentence, then the actions in order, each as "
    "a '- ' line saying what to do this week and what it is worth, then one line on what is "
    "going well. Under 150 words. No advice the facts do not support."
)


def note(db: Session, caller: Caller, code: str, lang: str) -> dict:
    view, c, _ = build(db, code)
    state = svc.state(db)
    vault, codes = user_vault(db, state.policy)
    b = db.scalar(select(Branch).where(Branch.branch_code == code))
    tb = vault.token("BR", code, f"{view['branch']['name']} ({code})")
    if c.peer_scope == "district" and b and (d := db.get(District, b.district_id)):
        tp = vault.token("DIST", d.code, f"{d.name} district")
    elif b and b.division_id and (v := db.get(Division, b.division_id)):
        tp = vault.token("DIV", v.code, f"{v.name} division")
    else:
        tp = "the bank"
    facts = _masked(view, c, tb, tp)
    segs = [Segment("bank", facts)]
    if lang == "bn":
        segs.append(Segment("instruction", "বাংলায় লিখুন (write in Bangla script); keep tokens "
                                           "and numbers exactly as given."))
    r = gw.call(db, caller, GatewayRequest(
        purpose="coach_note", system=_SYSTEM, turns=[Turn("user", segs)], vault=vault,
        facts=differences_by_line(facts), numeric_codes=codes, max_tokens=500))
    return {"text": r.text, "grounded": r.grounded, "unverified": list(r.unverified),
            "provider": r.provider, "model": r.model, "sent": facts, "truncated": r.truncated}

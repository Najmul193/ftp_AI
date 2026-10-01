"""The bank's pulse: one score, what it is made of, the bank against the
industry, where it is heading, and the risks and openings worth money.

For the CEO and the board. Every part is computed and explained; the score
is a weighted average of parts a reader can check, not a black box:

    margin      our gross spread against the industry's (Bangladesh Bank)
    funding     our cost of deposits against the industry's deposit rate
    momentum    net FTP profit, the last 7 days against the 7 before
    growth      deposits: the month-end forecast against today
    pricing     the share of deposits paying under most banks (flight risk)

In the asker's scope: a division head sees their division's pulse.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.copilot import tools
from app.ai.copilot.result import compute
from app.ai.forecast import service as forecast
from app.ai.insights import detectors, engine
from app.ai.public import service as public
from app.domain.scope import ScopeFilter
from app.models import AggDailyBranch
from app.repositories.analytics import AnalyticsRepo

WEIGHTS = {"margin": 0.25, "funding": 0.20, "momentum": 0.20, "growth": 0.15, "pricing": 0.20}
#: Findings that are openings (money to gain) rather than risks.
OPENINGS = {"peer_loan_rate_low", "landing_profit"}


@dataclass(frozen=True)
class Part:
    key: str
    label: str
    score: int                  # 0..100, higher is healthier
    value: str
    explain: str


def _clamp(x: float) -> int:
    return int(max(0, min(100, round(x))))


def grade(score: int) -> str:
    return "A" if score >= 80 else "B" if score >= 65 else "C" if score >= 50 else "D" if score >= 35 else "E"


def _window(db: Session, scope: ScopeFilter, days: int, end_offset: int = 0):
    q = select(func.max(AggDailyBranch.business_date))
    if not scope.unrestricted:
        q = q.where(AggDailyBranch.branch_id.in_(scope.branch_ids or [-1]))
    latest = db.scalar(q)
    if latest is None:
        return None, None
    end = latest - timedelta(days=end_offset)
    start = end - timedelta(days=days - 1)
    a = AnalyticsRepo(db, scope)
    f = tools.Where().filters(db, start, end)
    tot = tools.rollup(a, f, "total").get(None)
    n = len(tools.rollup(a, f, "date"))
    return (compute(tot, n) if tot and n else None), latest


def _industry(db: Session) -> dict[str, Decimal | None]:
    ind = public.industry(db)
    return {k: (ind[k][-1]["value"] if ind.get(k) else None)
            for k in ("BD_IND_DEPOSIT", "BD_IND_ADVANCE", "BD_IND_SPREAD")}


def build(db: Session, scope: ScopeFilter, reader: engine.Reader, label: str) -> dict:
    now, latest = _window(db, scope, 30)
    if now is None:
        return {"available": False, "reason": "no bank data in your part of the bank yet"}
    wk, _ = _window(db, scope, 7)
    prev, _ = _window(db, scope, 7, end_offset=7)
    ind = _industry(db)
    parts: list[Part] = []

    spread = now.get("spread")
    if spread is not None and ind["BD_IND_SPREAD"] is not None:
        gap = float(spread - ind["BD_IND_SPREAD"])
        parts.append(Part("margin", "Margin against the industry", _clamp(60 + gap * 15),
                          f"{spread:.2f}% vs {ind['BD_IND_SPREAD']:.2f}%",
                          "Our gross spread (yield on advances less cost of deposits) against "
                          "all scheduled banks'."))
    cod = now.get("cost_of_deposits")
    if cod is not None and ind["BD_IND_DEPOSIT"] is not None:
        gap = float(ind["BD_IND_DEPOSIT"] - cod)
        parts.append(Part("funding", "Funding cost against the industry", _clamp(60 + gap * 15),
                          f"{cod:.2f}% vs {ind['BD_IND_DEPOSIT']:.2f}%",
                          "Our cost of deposits against the industry's average deposit rate: "
                          "cheaper funding scores higher."))
    if wk and prev and prev.get("net_ftp_profit"):
        ch = float((wk["net_ftp_profit"] - prev["net_ftp_profit"]) / abs(prev["net_ftp_profit"]) * 100)
        parts.append(Part("momentum", "Profit momentum", _clamp(60 + ch * 4),
                          f"{ch:+.1f}% week on week",
                          "Net FTP profit over the last 7 days against the 7 before."))
    book = forecast.book_outlook(db, forecast.BookScope(scope, label=label),
                                 ("deposits", "net_ftp_profit"))
    dep = next((m for m in book.get("metrics", []) if m["metric"] == "deposits"), None) \
        if book.get("available") else None
    if dep and dep.get("month"):
        move = float((Decimal(dep["month"]["p50"]) - Decimal(dep["last"]["value"]))
                     / Decimal(dep["last"]["value"]) * 100)
        parts.append(Part("growth", "Deposit momentum", _clamp(60 + move * 10),
                          f"{move:+.2f}% to month-end (likely)",
                          f"Where deposits are heading by {book['month_end']}, from their own "
                          f"trend ({dep['confidence']} confidence)."))
    peers = public.book_vs_peers(db, branch_ids=None if scope.unrestricted else list(scope.branch_ids or []))
    deps = [p for p in peers if p["side"] == "LIABILITY"]
    total = sum((p["balance"] for p in deps), Decimal(0))
    if total:
        # The alerts' rule (detectors.peer_pricing): under most banks, or well
        # under the private banks, the closest competitors.
        def exposed_(p) -> bool:
            ref = p["pcb_median"] if p["pcb_median"] is not None else p["market_median"]
            return (p["p25"] is not None and p["our_rate"] < p["p25"]) or \
                p["our_rate"] - ref <= detectors.PEER_DEPOSIT_GAP
        exposed = sum((p["balance"] for p in deps if exposed_(p)), Decimal(0))
        share = float(exposed / total * 100)
        parts.append(Part("pricing", "Deposits priced against the market", _clamp(100 - share * 1.5),
                          f"{share:.0f}% of deposits pay well under other banks",
                          "Balances paying under most banks, or 75 bp or more under the private "
                          "banks, for the like product are the first to leave."))

    w = sum(WEIGHTS[p.key] for p in parts) or 1
    score = _clamp(sum(p.score * WEIGHTS[p.key] for p in parts) / w)

    feed = [i for i, m in engine.feed(db, reader, limit=60)
            if not (m and m.dismissed_at and m.dismissed_at >= i.raised_at)]
    money = lambda i: abs(Decimal(i.money_at_stake or 0))  # noqa: E731
    risks = sorted((i for i in feed if i.kind not in OPENINGS and i.severity != "info"),
                   key=money, reverse=True)[:3]
    opens = sorted((i for i in feed if i.kind in OPENINGS
                    or (i.kind == "landing_profit" and "beat" in i.title)), key=money, reverse=True)[:3]

    def item(i) -> dict:
        return {"id": i.id, "title": i.title, "severity": i.severity, "money": i.money_at_stake,
                "basis": i.money_basis, "action": i.action}

    versus = [
        {"label": "Cost of deposits", "ours": cod, "industry": ind["BD_IND_DEPOSIT"], "better": "lower"},
        {"label": "Yield on advances", "ours": now.get("yield_on_advances"),
         "industry": ind["BD_IND_ADVANCE"], "better": "higher"},
        {"label": "Gross spread", "ours": spread, "industry": ind["BD_IND_SPREAD"], "better": "higher"},
    ]
    return {
        "available": True, "label": label, "as_of": latest, "score": score, "grade": grade(score),
        "parts": [p.__dict__ | {"weight": WEIGHTS[p.key]} for p in parts],
        "versus": versus, "industry_month": (public.industry(db).get("BD_IND_SPREAD") or [{}])[-1].get("date"),
        "outlook": book if book.get("available") else None,
        "risks": [item(i) for i in risks], "openings": [item(i) for i in opens],
        "ratios": {k: now.get(k) for k in ("deposits", "advances", "nim", "cost_of_deposits",
                                           "yield_on_advances", "spread", "net_ftp_profit")},
    }

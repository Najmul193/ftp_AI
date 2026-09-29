"""Branch coach: where one branch stands among its peers, and what to do next.

The peer group is the branch's district: branches that share a market, a
cost of living and a customer base. Where a district has too few branches for
a median to mean anything (fewer than four), the division is used instead,
and the page says so. A branch is compared with its peers'
*median* -- not the best branch, which may be a head-office corporate branch
no rural branch can copy, and not the mean, which one outlier drags.

Every action is priced: what reaching the median would be worth a month, on
the branch's own balances. The biggest money comes first, so a manager with
ten minutes reads the one thing that matters.

PURE: no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from statistics import median

from app.ai.insights.detectors import pct, signed_bp, taka

_MONTH = Decimal(30) / Decimal(365)

#: Metric -> (label, higher is better).
METRICS: dict[str, tuple[str, bool]] = {
    "net_ftp_profit": ("Net FTP profit", True),
    "ftp_yield": ("FTP yield", True),
    "yield_on_advances": ("Yield on advances", True),
    "cost_of_deposits": ("Cost of deposits", False),
    "casa_share": ("CASA share of deposits", True),
    "nim": ("Net interest margin", True),
    "deposits": ("Deposits (average)", True),
    "advances": ("Advances (average)", True),
}


@dataclass
class BranchStats:
    code: str
    district: str
    division: int | None
    #: metric -> value this week (rates in %, money in taka).
    now: dict[str, Decimal | None]
    #: the same, the week before.
    before: dict[str, Decimal | None] = field(default_factory=dict)
    #: Cost of current+savings and of term deposits, % a year.
    casa_cost: Decimal | None = None
    term_cost: Decimal | None = None


@dataclass(frozen=True)
class Action:
    key: str
    title: str
    body: str
    money: Decimal | None          # per month, unless basis says otherwise
    basis: str
    metric: str


@dataclass
class Coaching:
    code: str
    rank: dict[str, tuple[int, int]]          # metric -> (rank, of)
    rank_before: dict[str, int]
    district_rank: dict[str, tuple[int, int]]
    district_median: dict[str, Decimal | None]
    bank_median: dict[str, Decimal | None]
    actions: list[Action]
    strengths: list[str]
    #: "district" or "division": which peer group the medians and ranks use.
    peer_scope: str = "district"
    peer_count: int = 0


def _median(vals) -> Decimal | None:
    v = [x for x in vals if x is not None]
    return Decimal(str(median(v))) if v else None


def _rank(stats: list[BranchStats], code: str, metric: str, higher: bool,
          when: str = "now") -> tuple[int, int] | None:
    vals = [(getattr(s, when).get(metric), s.code) for s in stats]
    vals = [(v, c) for v, c in vals if v is not None]
    if not any(c == code for _, c in vals):
        return None
    vals.sort(key=lambda p: p[0], reverse=higher)
    return next(i for i, (_, c) in enumerate(vals, 1) if c == code), len(vals)


#: A median of fewer peers than this is not a benchmark.
MIN_PEERS = 4

# Thresholds: small gaps are noise, not a coaching point.
COST_GAP = Decimal("0.10")          # percentage points
YIELD_GAP = Decimal("0.10")
CASA_GAP = Decimal("2")             # percentage points of share
RUNOFF = Decimal("-2")              # % week on week


def coach(me: BranchStats, everyone: list[BranchStats]) -> Coaching:
    peers = [s for s in everyone if s.district == me.district]
    scope = "district"
    if len(peers) < MIN_PEERS and me.division is not None:
        peers, scope = [s for s in everyone if s.division == me.division], "division"
    dmed = {m: _median(s.now.get(m) for s in peers) for m in METRICS}
    bmed = {m: _median(s.now.get(m) for s in everyone) for m in METRICS}
    rank, drank, before = {}, {}, {}
    for m, (_, higher) in METRICS.items():
        if (r := _rank(everyone, me.code, m, higher)) is not None:
            rank[m] = r
        if (r := _rank(peers, me.code, m, higher)) is not None:
            drank[m] = r
        if (r := _rank(everyone, me.code, m, higher, "before")) is not None:
            before[m] = r[0]

    n = me.now
    deposits, advances = n.get("deposits") or Decimal(0), n.get("advances") or Decimal(0)
    actions: list[Action] = []

    cod, med = n.get("cost_of_deposits"), dmed["cost_of_deposits"]
    if cod is not None and med is not None and cod - med >= COST_GAP and deposits:
        gap = cod - med
        actions.append(Action(
            "cost_of_deposits", "Bring the cost of deposits down to the peer median",
            f"Deposits cost {pct(cod)} a year here against a peer median of {pct(med)} "
            f"({signed_bp(gap)}). A cheaper mix -- more current and savings balances, fewer "
            f"high-rate term deposits at renewal -- closes the gap.",
            (deposits * gap / 100 * _MONTH).quantize(Decimal(1)), "per month", "cost_of_deposits"))

    share, smed = n.get("casa_share"), dmed["casa_share"]
    if share is not None and smed is not None and smed - share >= CASA_GAP and deposits:
        move = (smed - share) / 100 * deposits
        saving = None
        if me.term_cost is not None and me.casa_cost is not None and me.term_cost > me.casa_cost:
            saving = (move * (me.term_cost - me.casa_cost) / 100 * _MONTH).quantize(Decimal(1))
        actions.append(Action(
            "casa_share", "Grow current and savings accounts",
            f"Current and savings accounts are {share:.1f}% of deposits here; the peer "
            f"median is {smed:.1f}%. Moving about {taka(move)} of deposits into CASA reaches it"
            + (f", at {pct(me.casa_cost)} instead of {pct(me.term_cost)} a year." if saving else "."),
            saving, "per month", "casa_share"))

    yoa, ymed = n.get("yield_on_advances"), dmed["yield_on_advances"]
    if yoa is not None and ymed is not None and ymed - yoa >= YIELD_GAP and advances:
        gap = ymed - yoa
        actions.append(Action(
            "yield_on_advances", "Price loans closer to your peers",
            f"Loans earn {pct(yoa)} a year here against a peer median of {pct(ymed)} "
            f"({signed_bp(-gap)}). Repricing at renewal, starting with the largest facilities, "
            f"closes the gap.",
            (advances * gap / 100 * _MONTH).quantize(Decimal(1)), "per month", "yield_on_advances"))

    dep_now, dep_was = n.get("deposits"), me.before.get("deposits")
    if dep_now is not None and dep_was:
        ch = (dep_now - dep_was) / dep_was * 100
        if ch <= RUNOFF:
            actions.append(Action(
                "deposit_runoff", "Deposits are leaving",
                f"Average deposits fell {abs(ch):.1f}% on the week before, to {taka(dep_now)}. "
                f"Call the largest depositors and check which term deposits mature next.",
                (dep_was - dep_now).quantize(Decimal(1)), "of deposits", "deposits"))

    actions.sort(key=lambda a: a.money or Decimal(0), reverse=True)

    strengths = []
    for m, (label, higher) in METRICS.items():
        v, d = n.get(m), dmed.get(m)
        if v is None or d is None or m in ("deposits", "advances", "net_ftp_profit"):
            continue
        better = v - d if higher else d - v
        threshold = CASA_GAP if m == "casa_share" else Decimal("0.10")
        if better >= threshold:
            shown = f"{v:.1f}%" if m == "casa_share" else pct(v)
            med_text = f"{d:.1f}%" if m == "casa_share" else pct(d)
            strengths.append(f"{label} {shown}, better than the peer median of {med_text}.")
    r_now, r_was = rank.get("net_ftp_profit"), before.get("net_ftp_profit")
    if r_now and r_was and r_was > r_now[0]:
        strengths.append(f"Up {r_was - r_now[0]} place{'s' if r_was - r_now[0] > 1 else ''} in "
                         f"FTP profit, to #{r_now[0]} of {r_now[1]}.")

    return Coaching(me.code, rank, before, drank, dmed, bmed, actions[:3], strengths[:4],
                    scope, len(peers))

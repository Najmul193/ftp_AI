"""Where a product sits on the curve, and what the market pays there.

A product's FTP benchmark should track the market rate for money of the same
term: a 3-month deposit against the 91-day bill, a 5-year home loan against
the 5-year bond. The term is read from the product's name where it says
("TERM DEPOSIT - 3 MONTHS"), otherwise from its nature. It is an inference,
labelled as one, and a treasury desk will want to override it.

PURE: no I/O.
"""

from __future__ import annotations

import re
from decimal import Decimal

_TERM = re.compile(r"(\d+)\s*(day|week|month|year|yr)s?\b", re.I)
_UNIT_DAYS = {"day": 1, "week": 7, "month": 30, "year": 365, "yr": 365}

#: Days that snap to the conventional tenors, so "3 months" lands on 91.
_SNAP = {30: 30, 90: 91, 180: 182, 360: 364, 365: 364}


def infer_tenor_days(name: str, side: str, nature: str | None) -> tuple[int, str]:
    """(days, basis) for a product. Basis explains the choice in words."""
    m = _TERM.search(name or "")
    if m:
        days = int(m.group(1)) * _UNIT_DAYS[m.group(2).lower()]
        days = _SNAP.get(days, days)
        return days, f"term in the product name ({m.group(0).lower()})"
    low = (name or "").lower()
    if side == "LIABILITY":
        if (nature or "").upper() == "DEMAND" or any(w in low for w in ("current", "savings")):
            # Non-maturity deposits are not overnight money: their stable core
            # stays for years. One year is a common behavioural assumption.
            return 364, "non-maturity deposit, behavioural tenor assumed 1 year"
        if "pension" in low or "dps" in low:
            return 1825, "deposit scheme, assumed 5 years"
        return 91, "time deposit without a stated term, assumed 3 months"
    if any(w in low for w in ("overdraft", "revolving", "trust receipt", "latr", "murabaha")):
        return 91, "short-term revolving credit, assumed 3 months"
    if "home" in low or "mortgage" in low:
        return 1825, "home loan, assumed 5 years"
    if "personal" in low or "consumer" in low:
        return 730, "consumer loan, assumed 2 years"
    return 1095, "term loan without a stated term, assumed 3 years"


def curve_rate(points: list[tuple[int, Decimal]], days: int) -> tuple[Decimal, str] | None:
    """Market rate at `days`, linearly interpolated between curve points.

    Flat beyond either end: the curve is not extrapolated past what the
    market has actually priced.
    """
    pts = sorted(points)
    if not pts:
        return None
    if days <= pts[0][0]:
        return pts[0][1], f"{_label(pts[0][0])} (shortest point)"
    if days >= pts[-1][0]:
        return pts[-1][1], f"{_label(pts[-1][0])} (longest point)"
    for (d0, r0), (d1, r1) in zip(pts, pts[1:]):
        if d0 == days:
            return r0, _label(d0)
        if d0 < days < d1:
            w = Decimal(days - d0) / Decimal(d1 - d0)
            return (r0 + (r1 - r0) * w).quantize(Decimal("0.0001")), \
                f"between {_label(d0)} and {_label(d1)}"
        if d1 == days:
            return r1, _label(d1)
    return None


def _label(days: int) -> str:
    if days <= 1:
        return "overnight"
    if days < 30:
        return f"{days}D"
    if days < 365:
        return f"{days}D" if days in (91, 182, 364) else f"{round(days / 30)}M"
    return f"{round(days / 365)}Y"


def points_used(tenors: list[int], days: int) -> list[int]:
    """The curve tenors `curve_rate` reads for `days`: one, or the two either side."""
    ts = sorted(tenors)
    if not ts:
        return []
    if days <= ts[0]:
        return [ts[0]]
    if days >= ts[-1]:
        return [ts[-1]]
    if days in ts:
        return [days]
    lo = max(t for t in ts if t < days)
    hi = min(t for t in ts if t > days)
    return [lo, hi]

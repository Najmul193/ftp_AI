"""Which line of Bangladesh Bank's bank-by-bank tables a product compares with.

An inference from the product's name, nature and term -- labelled as one,
like the tenor it builds on (`market.tenor`).

PURE: no I/O.
"""

from __future__ import annotations

from decimal import Decimal

from app.ai.market.tenor import infer_tenor_days


def peer_key(name: str, side: str, nature: str | None) -> tuple[str, str] | None:
    """(book, product) in the peer tables, or None where there is no like line
    (current accounts pay next to nothing and are not tabled)."""
    low = (name or "").lower()
    if side == "LIABILITY":
        if "current" in low:
            return None
        if (nature or "").upper() == "DEMAND":
            return ("deposit", "snd_lt1cr") if ("notice" in low or "snd" in low) \
                else ("deposit", "savings")
        days, _ = infer_tenor_days(name, side, nature)
        if days < 182:
            return "deposit", "fd_3m"
        if days < 364:
            return "deposit", "fd_6m"
        if days < 730:
            return "deposit", "fd_1y"
        if days < 1095:
            return "deposit", "fd_2y"
        return "deposit", "fd_3y"
    if "home" in low or "mortgage" in low or "housing" in low:
        return "lending", "housing"
    if "card" in low:
        return "lending", "card"
    if any(w in low for w in ("personal", "consumer", "auto", "car loan")):
        return "lending", "consumer"
    if "export" in low:
        return "lending", "export"
    if any(w in low for w in ("trust receipt", "latr", "murabaha", "import", "trade")):
        return "lending", "trade"
    if "agri" in low:
        return "lending", "agriculture"
    sme = "sme" in low or "small" in low
    if any(w in low for w in ("overdraft", "revolving", "working", "cash credit")):
        return "lending", "wc_small" if sme else "wc_large"
    return "lending", "term_small" if sme else "term_large"


def mid(low: Decimal, high: Decimal) -> Decimal:
    return ((Decimal(low) + Decimal(high)) / 2).quantize(Decimal("0.01"))


def quantile(values: list[Decimal], q: float) -> Decimal | None:
    """Linear-interpolated quantile, q in [0, 1]."""
    v = sorted(values)
    if not v:
        return None
    pos = (len(v) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(v) - 1)
    w = Decimal(str(pos - lo))
    return (v[lo] + (v[hi] - v[lo]) * w).quantize(Decimal("0.01"))


def standing(values: dict[str, Decimal], bank: str | None, *, higher_first: bool) -> dict:
    """Median, quartiles and one bank's rank among `values` (bank -> rate).

    Deposits rank highest-paying first (what a depositor shops for), loans
    cheapest first (what a borrower shops for)."""
    vals = list(values.values())
    out = {"banks": len(vals), "median": quantile(vals, 0.5), "p25": quantile(vals, 0.25),
           "p75": quantile(vals, 0.75), "min": min(vals) if vals else None,
           "max": max(vals) if vals else None, "self": None, "rank": None}
    if bank and bank in values:
        me = values[bank]
        better = sum(1 for v in vals if (v > me if higher_first else v < me))
        out.update(self=me, rank=better + 1)
    return out

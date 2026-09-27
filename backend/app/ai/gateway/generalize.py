"""Blur amounts before they leave, so a figure cannot identify its owner.

A masked name is not enough on its own: "the branch with BDT 412,734,118.25 of
deposits" is re-identifiable by anyone who holds the bank's figures, because an
exact amount is a fingerprint. Three significant figures in crore keep what a
model needs -- magnitude and comparison -- and drop the fingerprint.

Rates are kept to two decimals: a rate is shared by thousands of accounts and
identifies nobody, and the model needs basis-point differences to reason.

PURE: no I/O.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal
from enum import Enum

CRORE = Decimal(10_000_000)
LAKH = Decimal(100_000)


class AmountMode(str, Enum):
    #: 3 significant figures, in crore: `412.7 cr` becomes `413 cr`.
    CRORE_3SF = "crore_3sf"
    #: Indexed so the largest value in a set is 100. No absolute size at all.
    INDEX = "index"


def sig(x: Decimal | float | int, digits: int = 3) -> Decimal:
    """Round to `digits` significant figures, half-even, as a Decimal."""
    d = Decimal(str(x))
    if d == 0:
        return Decimal(0)
    exp = d.adjusted() - digits + 1
    q = Decimal(1).scaleb(exp)
    return d.quantize(q, rounding=ROUND_HALF_EVEN)


def _plain(d: Decimal) -> str:
    """Decimal without exponent notation and without trailing zeros."""
    s = format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def crore(amount: Decimal | float | int, digits: int = 3) -> str:
    """BDT amount as `N cr` to `digits` significant figures."""
    return f"{_plain(sig(Decimal(str(amount)) / CRORE, digits))} cr"


def crore_value(amount: Decimal | float | int, digits: int = 3) -> Decimal:
    """The number `crore` prints, for grounding checks."""
    return sig(Decimal(str(amount)) / CRORE, digits)


def rate(r: Decimal | float | int, places: int = 2) -> str:
    """A percentage rate to `places` decimals, e.g. `8.79%`."""
    q = Decimal(1).scaleb(-places)
    return f"{_plain(Decimal(str(r)).quantize(q, rounding=ROUND_HALF_EVEN))}%"


def bps(delta_pct: Decimal | float | int) -> str:
    """A rate difference in basis points, signed: `+60 bp`."""
    b = (Decimal(str(delta_pct)) * 100).quantize(Decimal(1), rounding=ROUND_HALF_EVEN)
    return f"{'+' if b > 0 else ''}{b} bp"


def index(values: dict[str, Decimal | float | int]) -> dict[str, Decimal]:
    """Scale so the largest absolute value is 100, one decimal."""
    if not values:
        return {}
    top = max(abs(Decimal(str(v))) for v in values.values())
    if top == 0:
        return {k: Decimal(0) for k in values}
    return {k: (Decimal(str(v)) * 100 / top).quantize(Decimal("0.1"))
            for k, v in values.items()}


def amount(value: Decimal | float | int, mode: AmountMode = AmountMode.CRORE_3SF) -> str:
    """One amount under the configured mode (INDEX needs a set: use `index`)."""
    if mode is AmountMode.INDEX:
        raise ValueError("index mode applies to a set of values; call index()")
    return crore(value)

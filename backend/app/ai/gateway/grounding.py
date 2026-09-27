"""Every number in an answer must come from the facts the model was given.

A model that invents "NII rose 4.2%" is worse in a bank than one that says
nothing: the figure looks exactly like a real one. So the system computes and
the model narrates, and this module checks the narration: it pulls every
number out of the answer and looks for it among the numbers supplied.

Tolerant where a narrator reasonably would be -- rounding, a percentage written
as basis points, a difference between two supplied figures -- and strict
otherwise. Numbers that are not claims (years, days of the month, small counts
such as "3 actions", ordinals) are ignored.

PURE: no I/O.
"""

from __future__ import annotations

import re
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from itertools import combinations
from typing import Iterable

_BN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
_TOKEN = re.compile(r"\b(?:BR|PRD|DIST|DIV|ACCT|USER)_[0-9A-Z]{3}\b")
_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b")
_NUM = re.compile(r"(?<![\w.])[-+−]?\d{1,3}(?:,\d{2,3})*(?:\.\d+)?(?![\d])|(?<![\w.])[-+−]?\d+(?:\.\d+)?(?![\d])")
_ORDINAL = re.compile(r"^(st|nd|rd|th)\b")


@dataclass(frozen=True)
class Grounding:
    ok: bool
    checked: int
    unverified: tuple[str, ...]


def _to_dec(s: str) -> Decimal | None:
    try:
        return Decimal(s.replace(",", "").replace("−", "-").lstrip("+"))
    except InvalidOperation:
        return None


def numbers_in(text: str) -> list[tuple[str, Decimal, str]]:
    """(raw, value, trailing context) for each number in `text`."""
    t = _TOKEN.sub(" ", _DATE.sub(" ", text.translate(_BN_DIGITS)))
    out = []
    for m in _NUM.finditer(t):
        v = _to_dec(m.group(0))
        if v is None:
            continue
        out.append((m.group(0), v, t[m.end():m.end() + 6].lower()))
    return out


def _ignorable(v: Decimal, after: str) -> bool:
    if _ORDINAL.match(after):
        return True
    if v == v.to_integral_value() and 0 <= v <= 12 and "%" not in after[:2]:
        return True       # small counts: "3 actions", "top 5"
    if v == v.to_integral_value() and 1990 <= v <= 2100:
        return True       # years
    return False


def _close(a: Decimal, b: Decimal) -> bool:
    if a == b:
        return True
    diff = abs(a - b)
    # A narrator rounds to the precision it prints. Accept anything within
    # half a unit of the last printed digit, or 0.5% relative, whichever is
    # looser.
    exp = a.as_tuple().exponent
    unit = Decimal(1).scaleb(exp) / 2 if isinstance(exp, int) else Decimal(0)
    return diff <= max(unit, abs(b) * Decimal("0.005"))


def expand_facts(facts: Iterable[Decimal], *, with_differences: bool = True) -> set[Decimal]:
    """The supplied numbers plus the forms a narrator may reasonably write.

    Absolute values (a fall written without its sign), percentage points as
    basis points and back, and -- for small fact sets -- pairwise differences,
    so "up 60 bp from 8.19% to 8.79%" verifies from its two endpoints.
    """
    base = {Decimal(str(f)) for f in facts}
    out = set(base)
    for f in base:
        out.add(abs(f))
        out.add(f * 100)          # 0.6 pp -> 60 bp
        out.add(f / 100)
    if with_differences and len(base) <= 300:
        for a, b in combinations(base, 2):
            d = abs(a - b)
            out.add(d)
            out.add(d * 100)
    return out


def check(answer: str, facts: Iterable[Decimal], *, extra_allowed: Iterable[Decimal] = ()) -> Grounding:
    allowed = sorted({abs(a) for a in expand_facts(facts)}
                     | {abs(Decimal(str(x))) for x in extra_allowed})
    unverified: list[str] = []
    checked = 0
    for raw, v, after in numbers_in(answer):
        if _ignorable(v, after):
            continue
        checked += 1
        av = abs(v)
        # Only neighbours within the widest tolerance `_close` could accept.
        exp = av.as_tuple().exponent
        unit = Decimal(1).scaleb(exp) / 2 if isinstance(exp, int) else Decimal(0)
        tol = max(unit, av * Decimal("0.006"))
        lo, hi = bisect_left(allowed, av - tol), bisect_right(allowed, av + tol)
        if not any(_close(av, a) for a in allowed[lo:hi]):
            unverified.append(raw)
    return Grounding(ok=not unverified, checked=checked, unverified=tuple(unverified))

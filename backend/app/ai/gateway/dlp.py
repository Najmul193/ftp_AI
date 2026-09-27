"""The last check before text leaves: does it still contain anything real?

Tokenising is the mechanism; this is the proof. It scans the finished outbound
text for every real spelling the request's vault knows about, and for shapes
that look like account numbers, and blocks the call on any hit. Blocking, not
redacting: a hit means an earlier layer failed, and silently patching the
symptom would hide the fault that caused it.

What *is* redacted is the record of the blocked request: the egress log keeps
the payload so an auditor can see what was stopped, and it must not become a
new copy of the identifier it stopped. `redact` does that.

Text is scanned by segment kind. Bank-derived and user-typed segments are
checked against every known identifier. Public segments (news headlines) are
not -- a news story may well name a Dhaka neighbourhood that is also a branch
name, and that is public information -- but every segment is checked for
account-number shapes, because those have no business in any prompt.

PURE: no I/O.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

#: Nine or more consecutive digits, allowing the separators people type in
#: account numbers. Amounts never reach this length: the generaliser prints
#: them in crore to three significant figures.
_ACCOUNT_SHAPE = re.compile(r"(?<![\d.])\d(?:[ -]?\d){8,}(?![\d.])")
#: Things that look like email addresses or phone numbers.
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_PHONE_BD = re.compile(r"(?<!\d)(?:\+?880|0)1[3-9]\d{8}(?!\d)")

_EVERYWHERE = ("instruction", "public", "bank", "user")
_BANK = ("bank", "user")


@dataclass(frozen=True, slots=True)
class Hit:
    rule: str
    segment: int
    #: Never the matched text itself: the finding is logged, and logging the
    #: identifier would be the leak this module exists to prevent.
    hint: str


@dataclass(frozen=True)
class _Rule:
    name: str
    rx: re.Pattern[str]
    kinds: tuple[str, ...]
    #: Which group holds the sensitive part (0 = whole match).
    group: int = 0


def _mask_hint(s: str) -> str:
    return f"{len(s)} chars, starts {s[:1]!r}"


def _rules(known: Iterable[str], numeric_known: Iterable[str]) -> list[_Rule]:
    rules = [_Rule("account_number_shape", _ACCOUNT_SHAPE, _EVERYWHERE),
             _Rule("email", _EMAIL, _EVERYWHERE),
             _Rule("phone", _PHONE_BD, _EVERYWHERE)]
    names = sorted({k.strip() for k in known if k and len(k.strip()) >= 3
                    and not k.strip().isdigit()}, key=len, reverse=True)
    if names:
        rules.append(_Rule("known_identifier", re.compile(
            r"(?i)(?<![\w])(?:" + "|".join(map(re.escape, names)) + r")(?![\w])"), _BANK))
    codes = sorted({c.strip() for c in numeric_known if c and c.strip().isdigit()},
                   key=len, reverse=True)
    if codes:
        rules.append(_Rule("known_code", re.compile(
            r"(?i)\b(?:branch|br|code|sol)\s*(?:no\.?|#|:|-)?\s*("
            + "|".join(map(re.escape, codes)) + r")\b"), _BANK, group=1))
    return rules


def scan(
    segments: Iterable[tuple[str, str]],
    known: Iterable[str],
    *,
    numeric_known: Iterable[str] = (),
) -> list[Hit]:
    """Scan `(kind, text)` segments. Kinds: instruction, public, bank, user.

    `known` are real names and codes that must not appear in bank/user text.
    `numeric_known` are purely numeric identifiers (branch codes); they are
    matched only where they read as a code, since "142" is also a number.
    """
    rules = _rules(known, numeric_known)
    hits: list[Hit] = []
    for i, (kind, text) in enumerate(segments):
        for r in rules:
            if kind in r.kinds:
                hits += [Hit(r.name, i, _mask_hint(m.group(r.group)))
                         for m in r.rx.finditer(text)]
    return hits


def redact(kind: str, text: str, known: Iterable[str], *,
           numeric_known: Iterable[str] = ()) -> str:
    """`text` with every finding replaced by `[REDACTED:<rule>]`."""
    out = text
    for r in _rules(known, numeric_known):
        if kind not in r.kinds:
            continue
        if r.group:
            out = r.rx.sub(lambda m, n=r.name: m.group(0)[:m.start(1) - m.start(0)]
                           + f"[REDACTED:{n}]", out)
        else:
            out = r.rx.sub(f"[REDACTED:{r.name}]", out)
    return out

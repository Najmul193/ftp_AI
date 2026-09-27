"""Tag a headline with what it means for a funds-transfer-pricing book.

Deterministic keyword rules, not a model: free, instant, explainable, and
good enough to rank a news feed. A model reads the top stories later, in the
market digest, where judgement pays for itself.

PURE: no I/O.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: tag -> (pattern, weight for an FTP desk, what it touches in the book)
RULES: dict[str, tuple[str, int, str]] = {
    "policy_rate": (r"policy rate|repo rate|monetary policy|\bmps\b|rate (?:hike|cut)|"
                    r"(?:raises?|cuts?|holds?|keeps?|hikes?) (?:the )?(?:key |policy |interest )?rates?",
                    3, "FTP benchmark"),
    "liquidity": (r"call money|liquidity|interbank|money market|\bsdf\b|\bslf\b|"
                  r"reverse repo|\bdommr\b|\bbofr\b|special repo", 3, "Funding cost"),
    "govt_securities": (r"treasury bill|t-?bills?|treasury bond|t-?bonds?|bond yields?|"
                        r"govt securities|government securities|auction", 3, "Market curve"),
    "deposits": (r"deposits?|savings|\bfdr\b|term deposit|\bcasa\b|sanchayapatra|"
                 r"savings certificates?", 2, "Deposit pricing"),
    "credit": (r"\bloans?\b|lending|credit growth|private sector credit|\bnpl\b|"
               r"default(?:ed)? loans?|classified loans?|advances", 2, "Advance yield"),
    "remittance": (r"remittance|expatriate|wage earners?|migrant", 2, "CASA inflows"),
    "inflation": (r"inflation|\bcpi\b|consumer prices|food prices", 2, "Real rates"),
    "fx": (r"exchange rate|dollar|\btaka\b|\bforex\b|reserves?|\bimf\b|crawling peg",
           1, "FX and reserves"),
    "regulation": (r"bangladesh bank|central bank|\bbb\b|circular|directive|regulator|"
                   r"governor", 1, "Regulation"),
    "global_rates": (r"\bfed\b|federal reserve|\bfomc\b|\becb\b|treasury yields?|\bsofr\b|"
                     r"rate decision", 1, "Global rates"),
    "energy": (r"\boil\b|brent|\blng\b|fuel|energy prices", 1, "Import bill"),
}
_COMPILED = {t: re.compile(p, re.I) for t, (p, _, _) in RULES.items()}

_UP = re.compile(r"\b(?:raise[sd]?|hike[sd]?|increase[sd]?|tighten\w*|rise[sn]?|rose|"
                 r"up|higher|surge[sd]?|jump(?:s|ed)?|climb(?:s|ed)?)\b", re.I)
_DOWN = re.compile(r"\b(?:cut[s]?|lower(?:s|ed)?|reduce[sd]?|eas(?:e[sd]?|ing)|fall[s]?|fell|"
                   r"down|decline[sd]?|drop(?:s|ped)?|slip(?:s|ped)?)\b", re.I)
_HOLD = re.compile(r"\b(?:hold[s]?|held|keeps?|kept|unchanged|steady)\b", re.I)

BD_SOURCES = ("daily star", "tbs", "business standard", "dhaka tribune", "prothom alo",
              "financial express", "new age", "bss", "bangladesh")


@dataclass(frozen=True)
class Tagged:
    tags: tuple[str, ...]
    impacts: tuple[str, ...]
    #: +1 rates/pressure up, -1 down, 0 held or unclear. Only for rate stories.
    rate_signal: int
    relevance: int


def tag(title: str, summary: str = "", source: str = "") -> Tagged:
    text = f"{title} {summary}"
    tags = [t for t, rx in _COMPILED.items() if rx.search(text)]
    relevance = sum(RULES[t][1] for t in tags)
    if relevance and any(s in source.lower() or s in title.lower() for s in BD_SOURCES):
        relevance += 1                         # local news moves a local book
    signal = 0
    if set(tags) & {"policy_rate", "liquidity", "govt_securities"}:
        head = title or text
        if _HOLD.search(head):
            signal = 0
        elif _UP.search(head) and not _DOWN.search(head):
            signal = 1
        elif _DOWN.search(head) and not _UP.search(head):
            signal = -1
    return Tagged(tuple(tags), tuple(dict.fromkeys(RULES[t][2] for t in tags)), signal,
                  min(relevance, 20))

"""Learning from readers: findings people keep marking "not useful" or
dismissing step down a severity, so the bell stops ringing for them.

Deliberately modest. A kind is demoted only after enough readers have said
so, by a clear margin; it is never promoted above what its rule says, never
hidden, and the finding says why it sits lower. The rules stay the rules --
feedback only changes how loudly they speak.

PURE: no I/O (the counts are read by `engine`).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from app.ai.insights.detectors import SEVERITIES, Finding

#: At least this many "not useful" or dismissals, and at least twice the
#: "useful" marks, before a kind speaks more quietly.
MIN_VOTES = 5
MARGIN = 2


@dataclass(frozen=True)
class Votes:
    useful: int = 0
    not_useful: int = 0
    dismissed: int = 0

    @property
    def against(self) -> int:
        return self.not_useful + self.dismissed


def demoted(v: Votes) -> bool:
    return v.against >= MIN_VOTES and v.against >= MARGIN * (v.useful + 1)


def adjust(found: list[Finding], votes: dict[str, Votes]) -> list[Finding]:
    """The findings with demoted kinds one severity lower, and saying so."""
    out = []
    for f in found:
        v = votes.get(f.kind)
        if v is not None and demoted(v) and f.severity != SEVERITIES[-1]:
            lower = SEVERITIES[SEVERITIES.index(f.severity) + 1]
            f = replace(f, severity=lower,
                        body=f"{f.body} (Shown one level lower: {v.against} reader marks said "
                             f"findings like this were not useful, against {v.useful} useful.)")
        out.append(f)
    return out


def calibration(hits: int, scored: int, target: float = 0.8) -> float:
    """How much to widen (>1) or narrow (<1) forecast bands, from how often
    outcomes have fallen inside them. Needs a record of 10 before it acts;
    moves at most 1.5x either way."""
    if scored < 10:
        return 1.0
    rate = hits / scored
    if rate < target - 0.1:
        return min(1.5, 1 + (target - rate))
    if rate > min(0.97, target + 0.15):
        return max(1 / 1.5, 1 - (rate - target) / 2)
    return 1.0

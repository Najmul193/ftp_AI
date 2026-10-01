"""Learning from readers and from the forecasts' own record."""

from __future__ import annotations

from decimal import Decimal as D

from app.ai.insights import learning
from app.ai.insights.detectors import Finding


def _f(kind="peer_deposit_rate", sev="serious") -> Finding:
    return Finding(kind=kind, subject="X", severity=sev, title="t", body="b",
                   money_at_stake=D(1))


def test_a_kind_readers_reject_speaks_one_level_lower_and_says_why():
    v = {"peer_deposit_rate": learning.Votes(useful=1, not_useful=3, dismissed=2)}
    [f] = learning.adjust([_f()], v)
    assert f.severity == "warning" and "not useful" in f.body


def test_too_few_or_mixed_votes_change_nothing():
    for v in (learning.Votes(0, 2, 2), learning.Votes(4, 4, 1)):
        [f] = learning.adjust([_f()], {"peer_deposit_rate": v})
        assert f.severity == "serious"


def test_info_cannot_go_lower_and_other_kinds_are_untouched():
    v = {"peer_deposit_rate": learning.Votes(0, 9, 0)}
    a, b = learning.adjust([_f(sev="info"), _f(kind="margin_squeeze")], v)
    assert a.severity == "info" and b.severity == "serious"


def test_calibration_widens_when_outcomes_miss_and_narrows_when_bands_are_loose():
    assert learning.calibration(5, 9) == 1.0                 # too short a record
    assert learning.calibration(10, 20) > 1.2                # 50% inside: widen
    assert learning.calibration(16, 20) == 1.0               # 80%: as promised
    assert learning.calibration(20, 20) < 1.0                # always inside: narrow
    assert 1 / 1.5 <= learning.calibration(200, 200) and learning.calibration(0, 50) <= 1.5

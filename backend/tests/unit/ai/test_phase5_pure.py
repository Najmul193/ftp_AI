"""Branch coach rules, the upload day-on-day screen, and "Why?" preset plans."""

from __future__ import annotations

from datetime import date
from decimal import Decimal as D

from app.ai.coach.rules import BranchStats, coach
from app.ai.copilot.chat import preset_plan
from app.ai.uploads import compare_days

PRIOR = date(2026, 9, 22)


# --- branch coach ------------------------------------------------------------------- #

def branch(code, district="1", division=1, **now) -> BranchStats:
    base = {"net_ftp_profit": D(100_000), "ftp_yield": D("3.3"), "yield_on_advances": D("14.0"),
            "cost_of_deposits": D("4.2"), "casa_share": D("50"), "nim": D("9"),
            "deposits": D(500_000_000), "advances": D(400_000_000)}
    base.update({k: D(str(v)) for k, v in now.items()})
    return BranchStats(code, district, division, now=base, before=dict(base),
                       casa_cost=D("0.5"), term_cost=D("7.5"))


def peers(me: BranchStats) -> list[BranchStats]:
    return [me] + [branch(f"P{i}") for i in range(4)]


def test_dear_deposits_become_the_first_priced_action():
    me = branch("ME", cost_of_deposits="4.8")          # 60 bp over the median of 4.2
    c = coach(me, peers(me))
    a = c.actions[0]
    assert a.key == "cost_of_deposits" and a.basis == "per month"
    # 500,000,000 x 0.60% x 30/365
    assert a.money == D(246575)


def test_low_casa_is_priced_at_the_gap_between_term_and_casa_cost():
    me = branch("ME", casa_share="40")                  # 10 points under 50
    [a] = coach(me, peers(me)).actions
    # moving 10% of 500m = 50m from 7.5% to 0.5% a year, for a month
    assert a.key == "casa_share" and a.money == D(287671)


def test_the_largest_money_comes_first_and_only_three_are_kept():
    me = branch("ME", cost_of_deposits="4.4", casa_share="30", yield_on_advances="13",
                deposits="500000000")
    me.before["deposits"] = D(600_000_000)              # and deposits fell 16.7%
    c = coach(me, peers(me))
    assert len(c.actions) == 3
    assert [a.money for a in c.actions] == sorted((a.money for a in c.actions), reverse=True)


def test_a_branch_at_or_above_its_peers_gets_strengths_not_actions():
    me = branch("ME", cost_of_deposits="3.5", casa_share="65", ftp_yield="4")
    c = coach(me, peers(me))
    assert c.actions == [] and any("Cost of deposits 3.50%" in s for s in c.strengths)


def test_too_few_district_peers_falls_back_to_the_division():
    me = branch("ME", district="9", division=7)
    others = [branch("X1", district="9", division=7)] + \
             [branch(f"D{i}", district=str(i), division=7) for i in range(4)] + \
             [branch("FAR", district="50", division=2)]
    c = coach(me, [me] + others)
    assert c.peer_scope == "division" and c.peer_count == 6


def test_ranks_and_movement():
    me = branch("ME", net_ftp_profit="300000")
    me.before["net_ftp_profit"] = D(50_000)
    c = coach(me, peers(me))
    assert c.rank["net_ftp_profit"] == (1, 5) and c.rank_before["net_ftp_profit"] == 5
    assert any("Up 4 places" in s for s in c.strengths)


# --- upload day-on-day screen ------------------------------------------------------- #

def day(**branches):
    """branch code -> (deposits, loans, accounts)"""
    out = {}
    for b, (dep, loan, n) in branches.items():
        out[(b, "L")] = (D(dep), n)
        out[(b, "A")] = (D(loan), n // 4)
    return out


NAMES = {"101": "Gulshan (101)", "102": "Motijheel (102)", "103": "Uttara (103)"}


def test_a_clean_day_raises_nothing():
    was = day(**{"101": (5e8, 4e8, 400), "102": (3e8, 2e8, 300), "103": (2e8, 1e8, 200)})
    now = day(**{"101": (5.1e8, 4e8, 402), "102": (3e8, 2.05e8, 300), "103": (2e8, 1e8, 201)})
    assert compare_days(now, was, PRIOR, NAMES) == (None, [])


def test_a_vanished_branch_and_a_lost_zero_are_serious():
    was = day(**{"101": (5e8, 4e8, 400), "102": (3e8, 2e8, 300), "103": (2e8, 1e8, 200)})
    now = day(**{"101": (5e7, 4e8, 400), "102": (3e8, 2e8, 300)})    # 101 lost a zero, 103 gone
    note, f = compare_days(now, was, PRIOR, NAMES)
    kinds = {(x["kind"], x["branch"]) for x in f if x["severity"] == "serious"}
    assert note is None
    assert ("branch_missing", "103") in kinds and ("branch_move", "101") in kinds
    assert any(x["kind"] == "book_move" for x in f)                    # the whole book fell too
    assert any(x["title"] == "Gulshan (101): deposits down 90% in a day" for x in f)


def test_small_branches_moving_a_lot_are_not_noise():
    was = day(**{"101": (5e8, 4e8, 400), "102": (6e6, 2e6, 30)})
    now = day(**{"101": (5e8, 4e8, 400), "102": (3e6, 2e6, 30)})    # -50%, but only ৳30 lakh
    assert compare_days(now, was, PRIOR, NAMES)[1] == []


def test_a_completion_file_is_not_screened_branch_by_branch():
    was = day(**{"101": (5e8, 4e8, 400), "102": (3e8, 2e8, 300)})
    now = day(**{"102": (1e6, 0, 12)})
    note, f = compare_days(now, was, PRIOR, NAMES)
    assert f == [] and "completion file" in note


# --- "Why?" presets --------------------------------------------------------------- #

def test_a_preset_takes_the_pages_dates():
    p = preset_plan({"plan": {"tool": "why", "by": "product"},
                     "filters": {"date_from": "2026-09-10", "date_to": "2026-09-23"}})
    assert p.tool == "why" and p.period == "custom"
    assert (p.date_from, p.date_to) == (date(2026, 9, 10), date(2026, 9, 23))


def test_a_preset_without_dates_keeps_the_default_week_and_its_side():
    p = preset_plan({"plan": {"tool": "compare", "metrics": ["cost_of_deposits"], "by": "product",
                              "side": "LIABILITY", "compare": True}})
    assert p.period == "last_7_days" and p.side == "LIABILITY" and p.compare

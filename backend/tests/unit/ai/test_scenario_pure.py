"""The scenario engine: exact against the platform's own calculation, and sane."""

from __future__ import annotations

from decimal import Decimal as D

import pytest

from app.ai.scenario import engine as E
from app.domain.calculation import calculate
from app.domain.types import RateComponent, RateSource, RawRow, ResolvedRates, Side


def _rates(b: str, l: str = "0.30", o: str = "0.05") -> ResolvedRates:
    c = lambda v: RateComponent(D(v), RateSource.PRODUCT_OVERRIDE, 1)  # noqa: E731
    return ResolvedRates(c(b), c(l), c(o))


ACCOUNTS = [  # (side, balance, roi)
    ("LIABILITY", "1250000.00", "7.00"), ("LIABILITY", "380000.50", "6.50"),
    ("LIABILITY", "9100000.00", "7.25"), ("ASSET", "5400000.00", "13.50"),
    ("ASSET", "720000.00", "14.25"),
]


def _income(bench_shift: D = D(0)) -> D:
    """FTP income for the accounts, one day, through the platform's engine."""
    tot = D(0)
    for side, bal, roi in ACCOUNTS:
        row = RawRow(1, balance=D(bal), roi=D(roi))
        r = calculate(row, Side(side), _rates(str(D("8.50") + bench_shift)))
        tot += r.ftp_income
    return tot


def _lines() -> list[E.Line]:
    """The same accounts rolled up as the aggregates do: balance-weighted rates."""
    out = []
    for side in ("LIABILITY", "ASSET"):
        acc = [(D(b), D(r)) for s, b, r in ACCOUNTS if s == side]
        bal = sum(b for b, _ in acc)
        roi = sum(b * r for b, r in acc) / bal
        out.append(E.Line("0101", "P" + side[0], side, "TIME" if side == "LIABILITY" else None,
                          365, bal, roi, D("8.50"), D("0.30"), D("0.05")))
    return out


def test_zero_scenario_reproduces_the_platform_exactly():
    base = sum(E.apply(ln, E.Scenario()).ftp_base for ln in _lines())
    assert abs(base - _income()) < D("0.05")          # per-row rounding only
    unchanged = sum(E.apply(ln, E.Scenario()).ftp_new for ln in _lines())
    assert unchanged == base


def test_a_benchmark_shock_matches_a_full_recalculation():
    s = E.Scenario(market_bp=40, bench_follow=1.0, deposit_pass=0, demand_pass=0, loan_pass=0,
                   elasticity=0)
    shocked = sum(E.apply(ln, s).ftp_new for ln in _lines())
    assert abs(shocked - _income(D("0.40"))) < D("0.05")


def test_benchmark_moves_shift_profit_between_branches_and_treasury_not_bank_nii():
    s = E.Scenario(product_bench_bp={"PL": 100}, elasticity=0)
    r = E.run(_lines(), s, market_rate=D("8"))
    assert r["change"]["bank_nii"] == D(0)
    assert r["change"]["branch_ftp"] == -r["change"]["treasury"] != D(0)


def test_a_hike_lifts_nii_when_loans_reprice_faster_than_deposits():
    lines = [E.Line("1", "LN", "ASSET", None, 91, D("1e9"), D("13"), D("9"), D("0.3"), D("0.05")),
             E.Line("1", "TD", "LIABILITY", "TIME", 365, D("1e9"), D("7"), D("9"), D("0.3"),
                    D("0.05"))]
    r = E.run(lines, E.Scenario(market_bp=100, elasticity=0), market_rate=D("8"))
    assert r["change"]["bank_nii"] > 0
    w = {x["key"]: x["value"] for x in r["waterfall"]}
    assert w["loan_rates"] > 0 > w["deposit_rates"]
    assert abs(sum(w.values()) - r["change"]["bank_nii"]) < D("0.05")


def test_falling_behind_other_banks_costs_deposits_and_paying_more_wins_some():
    # A tenor inside the horizon: the whole book has rolled over and compared.
    td = E.Line("1", "TD", "LIABILITY", "TIME", 30, D("1e9"), D("7"), D("9"), D("0.3"), D("0.05"))
    lose = E.apply(td, E.Scenario(competitor_bp=100, elasticity=2))
    assert lose.balance == D("1e9") * (1 - D("0.02"))
    win = E.apply(td, E.Scenario(product_rate_bp={"TD": 100}, elasticity=2))
    assert win.balance == D("1e9") * (1 + D("0.01"))


def test_repricing_is_gradual_for_long_tenors():
    long = E.Line("1", "TD3", "LIABILITY", "TIME", 1095, D("1e9"), D("8"), D("9"), D("0.3"),
                  D("0.05"))
    r = E.apply(long, E.Scenario(market_bp=100, deposit_pass=1.0, elasticity=0, horizon_months=3))
    assert r.repriced == D(90) / D(1095)
    assert D("8") < r.roi < D("8.1")


def test_branch_filter_leaves_other_branches_alone():
    a = E.Line("A", "TD", "LIABILITY", "TIME", 91, D("1e9"), D("7"), D("9"), D("0.3"), D("0.05"))
    b = E.Line("B", "TD", "LIABILITY", "TIME", 91, D("1e9"), D("7"), D("9"), D("0.3"), D("0.05"))
    s = E.Scenario(market_bp=100, branches=("A",))
    assert E.apply(a, s).benchmark != a.benchmark
    assert E.apply(b, s).benchmark == b.benchmark


def test_parse_checks_every_field():
    s = E.parse({"market_bp": "50", "loan_pass": 0.8, "product_rate_bp": {"TD": 25},
                 "horizon_months": 6}, products={"TD"})
    assert (s.market_bp, s.loan_pass, s.product_rate_bp, s.horizon_months) == (50, 0.8, {"TD": 25}, 6)
    for bad in ({"market_bp": 9000}, {"product_rate_bp": {"NOPE": 10}}, {"loan_pass": "x"},
                {"branches": ["Z"]}, "nonsense"):
        with pytest.raises(E.ScenarioError):
            E.parse(bad, products={"TD"}, branches={"A"})


def test_path_phases_in_to_the_horizon():
    lines = _lines()
    p = E.path(lines, E.Scenario(market_bp=100, horizon_months=1), market_rate=D("8"))
    assert p[-1]["day"] == 30 and len(p) == 5

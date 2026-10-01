"""Forecasts: the series model, the policy outlook, and the detectors that use them."""

from __future__ import annotations

from datetime import date
from decimal import Decimal as D

import numpy as np

from app.ai.forecast import policy, series
from app.ai.insights import detectors
from app.ai.insights.facts import FactSheet, Landing, PeerGap, PolicyView


# --- the series model ------------------------------------------------------------- #

def test_recovers_a_trend_inside_its_band():
    rng = np.random.default_rng(1)
    y = 100 + 0.5 * np.arange(500) + rng.normal(0, 2, 500)
    f = series.forecast(y, 60)
    truth = 100 + 0.5 * 559
    assert f.p10[-1] <= truth <= f.p90[-1]
    assert abs(f.p50[-1] - truth) < 10
    assert f.confidence in ("high", "medium")


def test_bands_widen_with_the_horizon_and_are_ordered():
    rng = np.random.default_rng(2)
    f = series.forecast(100 + np.cumsum(rng.normal(0, 1, 300)), 40)
    width = f.p90 - f.p10
    assert width[-1] > width[0]
    assert np.all(f.p10 <= f.p50) and np.all(f.p50 <= f.p90)


def test_backtest_coverage_is_near_the_promise():
    """P10-P90 should hold most outcomes on a plain random walk."""
    rng = np.random.default_rng(3)
    f = series.forecast(100 + np.cumsum(rng.normal(0, 1, 600)), 20)
    assert f.backtest.coverage is not None and f.backtest.coverage >= 0.6


def test_deterministic():
    y = list(100 + np.sin(np.arange(80) / 5) * 3)
    a, b = series.forecast(y, 10), series.forecast(y, 10)
    assert np.array_equal(a.p50, b.p50) and np.array_equal(a.p90, b.p90)


def test_prices_never_go_negative():
    rng = np.random.default_rng(4)
    y = 80 * np.exp(np.cumsum(rng.normal(0, 0.04, 400)))
    f = series.forecast(y, 120)
    assert f.p10.min() > 0


def test_bounds_hold():
    rng = np.random.default_rng(5)
    f = series.forecast(9 + np.cumsum(rng.normal(0, 0.2, 300)), 60, unit="rate", lo=7.5, hi=11.0)
    assert f.paths.min() >= 7.5 and f.paths.max() <= 11.0


def test_short_history_holds_the_last_value_and_says_so():
    f = series.forecast([5.0, 5.1, 5.05], 5, unit="rate")
    assert f.confidence == "low" and f.notes
    assert abs(f.p50[-1] - 5.05) < 0.2


def test_only_recent_history_is_used():
    y = np.concatenate([np.full(2000, 1000.0), 100 + np.arange(300) * 0.1])
    f = series.forecast(y, 5, max_history=300)
    assert f.n == 300 and f.p50[0] < 200


# --- the calendar ------------------------------------------------------------------ #

def test_business_days_skip_the_bangladesh_weekend():
    days = series.business_days_after(date(2026, 9, 23), date(2026, 9, 30))
    assert [d.isoformat() for d in days] == ["2026-09-24", "2026-09-27", "2026-09-28",
                                            "2026-09-29", "2026-09-30"]


def test_calendar_fill_carries_thursday_over_the_weekend():
    days = [date(2026, 9, 24), date(2026, 9, 27)]           # Thu, Sun
    cal, vals = series.calendar_fill(days, np.array([1.0, 2.0]), date(2026, 9, 23),
                                     date(2026, 9, 27))
    assert [d.day for d in cal] == [24, 25, 26, 27]
    assert list(vals) == [1.0, 1.0, 1.0, 2.0]


def test_month_and_quarter_end():
    assert series.month_end(date(2026, 2, 3)) == date(2026, 2, 28)
    assert series.quarter_end(date(2026, 11, 5)) == date(2026, 12, 31)
    assert series.quarter_end(date(2026, 9, 23)) == date(2026, 9, 30)


# --- the policy outlook -------------------------------------------------------------- #

def _inputs(**kw) -> policy.Inputs:
    base = dict(repo=D("9.50"), slf=D("11.00"), sdf=D("7.50"), last_mpc=date(2026, 9, 23),
                inflation_now=D("9.2"), inflation_now_year=2026, inflation_next=D("6.0"),
                call_money=D("8.74"), tbill_91=D("8.28"), tbill_364=D("8.80"),
                usdbdt_now=D("123"), usdbdt_month_ago=D("123"), news_signal=0, news_count=0,
                fed_now=D("3.88"), fed_quarter_ago=D("3.88"), today=date(2026, 10, 1))
    base.update(kw)
    return policy.Inputs(**base)


def test_implied_forward_matches_the_textbook():
    # 1 + 0.08*91/365 then a forward over 273 days reaching 1 + 0.09*364/365.
    f = policy.implied_forward(D("8"), 91, D("9"), 364)
    assert D("9.1") < f < D("9.4")


def test_odds_sum_to_100_and_lean_follows_them():
    for score in (-0.9, -0.3, 0.0, 0.2, 0.6):
        o = policy.odds(score)
        assert sum(o.values()) == 100
    hot = policy.assess(_inputs(inflation_now=D("12"), inflation_next=D("13"), call_money=D("10.8"),
                                usdbdt_month_ago=D("119"), news_signal=4, news_count=4))
    assert hot.leaning == "hike" and hot.odds["hike"] == max(hot.odds.values())
    cool = policy.assess(_inputs(inflation_now=D("5"), inflation_next=D("4"), call_money=D("7.6"),
                                 tbill_364=D("7.5"), news_signal=-3, news_count=3,
                                 fed_quarter_ago=D("4.5")))
    assert cool.leaning == "cut"


def test_missing_signals_are_named_not_guessed():
    o = policy.assess(_inputs(tbill_91=None, usdbdt_now=None))
    assert "T-bill yields" in o.missing and "USD/BDT" in o.missing
    assert all(d.key not in ("curve", "taka") for d in o.drivers)


def test_next_meeting_rolls_forward():
    d, _ = policy.next_meeting(date(2026, 3, 1), date(2026, 10, 1))
    assert d > date(2026, 10, 1) and (d - date(2026, 3, 1)).days % policy.MEETING_GAP_DAYS == 0


# --- the detectors that look ahead --------------------------------------------------- #

def _fs(**kw) -> FactSheet:
    fs = FactSheet(scope_key="HO", scope_label="Bank", today=date(2026, 10, 1),
                   business_date=date(2026, 9, 23))
    for k, v in kw.items():
        setattr(fs, k, v)
    return fs


def _gap(**kw) -> PeerGap:
    base = dict(product_code="TDR06", side="LIABILITY", peer_label="Fixed deposit 6–12 months",
                our_rate=D("7.00"), market_median=D("8.38"), pcb_median=D("8.50"), p25=D("6.72"),
                p75=D("9.00"), balance=D("916824606"), month=date(2026, 8, 1))
    base.update(kw)
    return PeerGap(**base)


def test_deposit_paying_under_the_private_banks_is_flagged_with_its_cost():
    [f] = detectors.peer_pricing(_fs(peer_gaps=[_gap()]))
    assert f.kind == "peer_deposit_rate" and "150 bp" in f.title
    # ৳91.7 crore x 1.50% x 30/365 ≈ ৳11.3 lakh a month.
    assert D("1100000") < f.money_at_stake < D("1160000")


def test_small_or_fairly_priced_products_are_left_alone():
    assert detectors.peer_pricing(_fs(peer_gaps=[_gap(balance=D("1000000"))])) == []
    assert detectors.peer_pricing(_fs(peer_gaps=[_gap(our_rate=D("8.40"))])) == []


def test_loans_priced_off_the_market_both_ways():
    high = _gap(product_code="COROD", side="ASSET", peer_label="Working capital",
                our_rate=D("14.95"), market_median=D("13.40"), pcb_median=D("13.75"),
                p25=D("12.0"), p75=D("14.0"), balance=D("1607533649"))
    low = _gap(product_code="HMLN5", side="ASSET", peer_label="Housing loan", our_rate=D("10.23"),
               market_median=D("13.00"), pcb_median=D("13.25"), p25=D("11.74"), p75=D("14.0"),
               balance=D("114000000"))
    kinds = {f.kind for f in detectors.peer_pricing(_fs(peer_gaps=[high, low]))}
    assert kinds == {"peer_loan_rate_high", "peer_loan_rate_low"}


def _landing(**kw) -> Landing:
    base = dict(metric="net_ftp_profit", label="Net FTP profit", unit="bdt", kind="flow",
                month_end=date(2026, 9, 30), last=D("1500000"), p10=D("40000000"),
                p50=D("42000000"), p90=D("44000000"), so_far=D("32000000"),
                previous_month=D("50000000"), confidence="medium")
    base.update(kw)
    return Landing(**base)


def test_profit_landing_below_last_month():
    [f] = detectors.landing(_fs(landings={"net_ftp_profit": _landing()}))
    assert f.kind == "landing_profit" and f.severity == "warning"
    assert f.money_at_stake == D("8000000")


def test_no_landing_finding_without_last_month():
    assert detectors.landing(_fs(landings={"net_ftp_profit": _landing(previous_month=None)})) == []


def test_policy_outlook_speaks_only_when_it_leans():
    calm = PolicyView("hold", {"hike": 10, "hold": 85, "cut": 5}, date(2026, 12, 23), D("9.5"))
    assert detectors.policy_outlook(_fs(policy=calm)) == []
    leaning = PolicyView("cut", {"hike": 5, "hold": 40, "cut": 55}, date(2026, 12, 23), D("9.5"),
                         ("The IMF expects inflation to ease.",))
    [f] = detectors.policy_outlook(_fs(policy=leaning))
    assert f.audience == "PUBLIC" and "cut" in f.title and "not a market price" in f.body

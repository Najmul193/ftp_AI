"""Ask FTP's pure parts: the plan, periods, metrics, and what a provider reads."""

from __future__ import annotations

from datetime import date
from decimal import Decimal as D

import pytest

from app.ai.copilot.catalog import PlanError, parse_plan, prior, window
from app.ai.copilot.result import Column, Result, chart_for, compute, masked_text, passes
from app.ai.gateway.tokenizer import Vault

LATEST, EARLIEST = date(2026, 9, 23), date(2026, 9, 3)


# --- the plan -------------------------------------------------------------------- #

def test_a_plain_plan_parses_with_defaults():
    p = parse_plan('{"tool":"compare","metrics":["net_ftp_profit"],"by":"branch","limit":5}')
    assert (p.tool, p.by, p.limit, p.period, p.compare) == ("compare", "branch", 5, "last_7_days", False)


def test_code_fences_and_chatter_around_the_json_are_tolerated():
    p = parse_plan('Here you go:\n```json\n{"tool":"trend","metrics":["nim"]}\n```')
    assert p.tool == "trend" and p.chart == "line"


@pytest.mark.parametrize("raw", ['{"tool":"sql","query":"drop table"}', "not json at all",
                                 '{"tool":"compare","by":"account"}', '["compare"]',
                                 '{"tool":"compare","period":"custom","date_from":"2026-09-10"}'])
def test_anything_outside_the_catalogue_is_refused(raw):
    with pytest.raises(PlanError):
        parse_plan(raw)


def test_unknown_metrics_are_dropped_not_run():
    p = parse_plan('{"tool":"compare","metrics":["password","nim"]}')
    assert p.metrics == ("nim",)


def test_a_change_condition_in_bp_implies_a_comparison():
    p = parse_plan('{"tool":"compare","metrics":["nim"],"by":"branch",'
                   '"where":[{"metric":"cost_of_deposits","op":"change_gt","value_bp":25}]}')
    assert p.compare and p.where[0].value == D("0.25")
    assert "cost_of_deposits" in p.metrics                  # shown, since it is filtered on


def test_limits_and_market_codes_are_clamped_to_the_catalogue():
    p = parse_plan('{"tool":"market","codes":["BB_CALL_ON","EVIL"],"limit":5000}')
    assert p.codes == ("BB_CALL_ON",) and p.limit == 100


def test_a_stored_plan_round_trips():
    p = parse_plan('{"tool":"compare","metrics":["cost_of_deposits"],"by":"branch","period":"this_month",'
                   '"where":[{"metric":"cost_of_deposits","op":"change_gt","value_bp":25}],'
                   '"sort":"change:cost_of_deposits"}')
    assert parse_plan(p.to_dict()) == p


# --- periods ------------------------------------------------------------------------ #

@pytest.mark.parametrize("period,expect", [
    ("latest_day", (LATEST, LATEST)),
    ("last_7_days", (date(2026, 9, 17), LATEST)),
    ("last_30_days", (EARLIEST, LATEST)),                   # clipped to the data
    ("this_month", (EARLIEST, LATEST)),
    ("all", (EARLIEST, LATEST)),
])
def test_periods_are_relative_to_the_latest_business_date(period, expect):
    assert window(parse_plan({"tool": "compare", "period": period}), LATEST, EARLIEST) == expect


def test_last_month_and_the_prior_window():
    assert window(parse_plan({"tool": "compare", "period": "last_month"}), LATEST, date(2026, 1, 1)) \
        == (date(2026, 8, 1), date(2026, 8, 31))
    assert prior(date(2026, 9, 17), LATEST) == (date(2026, 9, 10), date(2026, 9, 16))


# --- metrics -------------------------------------------------------------------------- #

def test_metrics_are_annualised_like_the_banking_ratios():
    # Two days of 36,500 divisor: balance_pd = balance / 36,500 summed.
    meas = {"interest_receivable": D("1000"), "interest_payable": D("400"),
            "asset_balance": D("7300000"), "liability_balance": D("7300000"),
            "asset_balance_pd": D("200"), "liability_balance_pd": D("200"),
            "total_balance_pd": D("400"), "net_ftp_profit": D("300")}
    m = compute(meas, days=2)
    assert m["yield_on_advances"] == D("5") and m["cost_of_deposits"] == D("2")
    assert m["nim"] == D("3") and m["spread"] == D("3")
    assert m["ftp_yield"] == D("0.75") and m["deposits"] == D("3650000")


def test_missing_bases_give_no_rate_rather_than_a_division_by_zero():
    assert compute({}, days=0)["cost_of_deposits"] is None


def test_conditions_on_level_and_change():
    c = parse_plan('{"tool":"compare","where":[{"metric":"nim","op":"change_lt","value_bp":-10}]}').where
    assert passes({"nim": D(8), "nim__change": D("-0.2")}, c)
    assert not passes({"nim": D(8), "nim__change": D("-0.05")}, c)
    assert not passes({"nim": D(8), "nim__change": None}, c)


# --- charts and masking --------------------------------------------------------------- #

def test_a_chart_never_mixes_units_on_one_axis():
    plan = parse_plan('{"tool":"trend","metrics":["nim","deposits"]}')
    cols = [Column("label", "Date", "date"), Column("nim", "NIM", "pct"),
            Column("deposits", "Deposits", "bdt")]
    spec = chart_for(plan, cols, [{"label": "2026-09-01", "nim": 1, "deposits": 2}])
    assert [s["key"] for s in spec["series"]] == ["nim"]


def test_sorting_by_a_change_charts_the_change():
    plan = parse_plan('{"tool":"compare","metrics":["nim"],"by":"branch","sort":"change:nim"}')
    cols = [Column("label", "Branch", "text"), Column("nim", "NIM", "pct"),
            Column("nim__prior", "before", "pct"), Column("nim__change", "Change", "pp")]
    spec = chart_for(plan, cols, [{"label": "a"}, {"label": "b"}])
    assert spec["series"][0]["key"] == "nim__change"


def test_the_provider_copy_has_tokens_and_blurred_amounts():
    v = Vault()
    res = Result(title="t", description="Net FTP profit by branch, {DIV:3}",
                 columns=[Column("label", "Branch", "text"), Column("p", "Net FTP profit", "bdt"),
                          Column("c", "Change", "pp")],
                 rows=[{"label": "Gulshan (0101)", "ent": "{BR:0101}", "p": D("969815.54"),
                        "c": D("0.2512")}],
                 period={"start": "2026-09-17", "end": "2026-09-23"})
    text = masked_text(res, lambda kind, key: v.token(kind, key))
    assert "Gulshan" not in text and "0101" not in text and "969815" not in text
    assert v.token("BR", "0101") in text and v.token("DIV", "3") in text
    assert "0.097 cr" in text and "+25 bp" in text

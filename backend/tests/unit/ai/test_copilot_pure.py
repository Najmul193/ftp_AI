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


# --- names that repeat, and plans that answer another question ------------------- #

from app.ai.copilot.chat import ambiguous_names  # noqa: E402
from app.ai.gateway.tokenizer import Entity  # noqa: E402


def _branches() -> Vault:
    v = Vault()
    v.register([Entity("BR", "100", "Dhaka Main (100)", ("Dhaka Main", "100")),
                Entity("BR", "105", "Dhaka Main (105)", ("Dhaka Main", "105")),
                Entity("BR", "111", "Chattogram GEC (111)", ("Chattogram GEC", "111"))])
    return v


def test_a_name_two_branches_share_is_asked_about():
    v = _branches()
    [(name, options)] = ambiguous_names("Why did Dhaka Main lose deposits while Chattogram GEC grew?", v)
    assert name == "dhaka main" and options == ["Dhaka Main (100)", "Dhaka Main (105)"]


def test_a_code_beside_the_name_settles_it_and_masks_to_that_branch():
    v = _branches()
    for q in ("How is Dhaka Main 105 doing?", "How is Dhaka Main (105) doing?", "How is dhaka main,105 doing?"):
        assert ambiguous_names(q, v) == []
        assert v.token("BR", "105") in v.mask_text(q) and v.token("BR", "100") not in v.mask_text(q)


def test_why_on_deposits_becomes_a_comparison_of_the_named_branches():
    p = parse_plan('{"tool":"why","metrics":["deposits"],"branches":["BR_AAA","BR_BBB"]}')
    assert p.tool == "compare" and p.by == "branch" and p.compare and p.metrics == ("deposits",)


def test_why_on_profit_stays_why():
    assert parse_plan('{"tool":"why","by":"product"}').tool == "why"


def test_conditions_that_cannot_both_hold_are_dropped():
    p = parse_plan('{"tool":"compare","metrics":["deposits"],"by":"branch","where":['
                   '{"metric":"deposits","op":"lt","value":0},{"metric":"deposits","op":"gt","value":0}]}')
    assert p.where == ()
    q = parse_plan('{"tool":"compare","metrics":["nim"],"where":['
                   '{"metric":"nim","op":"gt","value":8},{"metric":"nim","op":"lt","value":9}]}')
    assert len(q.where) == 2                                 # a real band is kept


def test_a_shorter_name_inside_a_longer_one_is_not_a_mention():
    v = _branches()
    v.register([Entity("DIST", "DHA", "Dhaka district", ("Dhaka",)),
                Entity("DIV", "D", "Dhaka division", ("Dhaka",))])
    assert ambiguous_names("How is Dhaka Main (100) doing?", v) == []
    [(name, _)] = ambiguous_names("Deposits in Dhaka this week?", v)
    assert name == "dhaka"


def test_the_planner_instructions_build_and_every_example_is_a_valid_plan():
    import json as _json
    from app.ai.copilot.catalog import planner_system
    text = planner_system()                       # raises if a brace is unescaped
    examples = [ln for ln in text.splitlines() if ln.startswith("{")]
    assert len(examples) >= 6
    for ln in examples:
        parse_plan(_json.loads(ln))               # each example is itself a valid plan


def test_wording_that_moves_a_figure_the_wrong_way_is_caught():
    from app.ai.copilot.result import direction_conflicts
    plan = parse_plan('{"tool":"compare","metrics":["deposits","cost_of_deposits"],"by":"branch","compare":true}')
    rows = [{"label": "Dhaka Main (100)", "deposits__change": D("576225.96"), "cost_of_deposits__change": D("-0.0418")},
            {"label": "Chattogram GEC (111)", "deposits__change": D("831741.88"), "cost_of_deposits__change": D("0.0011")}]
    qwen = ("Dhaka Main (100) lost deposits while Chattogram GEC (111) grew because Dhaka Main (100)'s "
            "deposits fell by 0.0576 cr, and Chattogram GEC (111)'s deposits rose by 0.0832 cr.\n"
            "- Dhaka Main (100)'s cost of deposits dropped by -4 bp.")
    assert direction_conflicts(qwen, plan, rows) == ["Dhaka Main (100) deposits"]
    honest = ("The figures do not show that: deposits rose at both branches. Dhaka Main (100)'s deposits "
              "rose by 0.0576 cr and Chattogram GEC (111)'s grew by 0.0832 cr.")
    assert direction_conflicts(honest, plan, rows) == []


# --- the page a question is asked on --------------------------------------------- #

from app.ai.copilot.chat import merge_where  # noqa: E402
from app.ai.copilot.catalog import PAGES, suggestions  # noqa: E402
from app.ai.copilot.tools import Where  # noqa: E402


def test_the_page_filters_fill_what_the_question_left_open():
    merged, used = merge_where(Where(), Where(division_id=2, side="LIABILITY"))
    assert used and merged.division_id == 2 and merged.side == "LIABILITY"


def test_a_place_named_in_the_question_replaces_the_pages_place_entirely():
    # Asking about a branch while the page is filtered to another division must
    # not intersect the two into nothing.
    merged, used = merge_where(Where(branch_codes=("111",)), Where(division_id=3, category="URBAN"))
    assert merged.branch_codes == ("111",) and merged.division_id is None and merged.category is None
    assert not used


def test_what_and_where_are_merged_separately():
    merged, used = merge_where(Where(product_codes=("TDR03",)), Where(division_id=3, side="ASSET"))
    assert merged.division_id == 3 and merged.product_codes == ("TDR03",) and merged.side is None
    assert used


def test_every_page_has_a_description_and_page_questions_come_first():
    assert all(label and what for label, what in PAGES.values())
    assert suggestions("HO", "coach")[0].startswith("Why is this branch")
    assert suggestions("HO", None) == suggestions("HO", "nowhere")

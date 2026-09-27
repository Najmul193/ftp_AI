"""Detectors, the standard brief and the provider renderer: pure, no database."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal as D

from app.ai.gateway import dlp
from app.ai.gateway.policy import FieldPolicy
from app.ai.gateway.tokenizer import Entity, Vault
from app.ai.insights import brief, detectors
from app.ai.insights.engine import scope_key_for, visible_keys
from app.ai.insights.facts import (
    BenchmarkGap, Delta, FactSheet, MarketPoint, Segment, fill_names,
)
from app.ai.insights.render import render
from app.domain.types import ScopeLevel

TODAY = date(2026, 9, 27)


def gap(code="HMLN5", name="HOME LOAN - 5 YEARS", side="ASSET", bench="7.5", market="8.645",
        gap_bp=-114, balance="114050125.74", impact="-107332", behavioural=False,
        as_of=date(2026, 9, 21), tenor=1825) -> BenchmarkGap:
    return BenchmarkGap(code, name, side, tenor, "term in the product name", D(bench), D(market),
                        "5Y", gap_bp, D(balance) if balance else None,
                        D(impact) if impact else None, behavioural, as_of)


def point(code, value, *, prev=None, prev_as_of=None, week=None, week_as_of=None,
          as_of=date(2026, 9, 24), short=None, unit="pct", category="bd_money",
          tenor=None, stale=False) -> MarketPoint:
    return MarketPoint(code, short or code, unit, category, D(value), as_of,
                       D(prev) if prev else None, prev_as_of, D(week) if week else None,
                       week_as_of, stale, tenor)


def sheet(**kw) -> FactSheet:
    fs = FactSheet(scope_key=kw.pop("scope_key", "HO"), scope_label="the whole bank", today=TODAY,
                   names={"PRD:HMLN5": "HOME LOAN - 5 YEARS", "BR:0101": "Gulshan (0101)",
                          "PRD:TDR03": "TERM DEPOSIT - 3 MONTHS"})
    for k, v in kw.items():
        setattr(fs, k, v)
    return fs


def book(fs: FactSheet, *, net=("1000000", "1000000"), nim=("8.86", "8.89"),
         cod=("4.26", "4.27"), deposits=("9185862304", "9154243425"),
         casa=("49.6", "49.7")) -> FactSheet:
    fs.business_date = date(2026, 9, 26)
    fs.window = (date(2026, 9, 20), date(2026, 9, 26))
    fs.prior_window = (date(2026, 9, 13), date(2026, 9, 19))
    fs.prior_has_data = True
    fs.profit = {"net": Delta(D(net[0]), D(net[1]))}
    fs.ratios = {"nim": Delta(D(nim[0]), D(nim[1])), "cost_of_deposits": Delta(D(cod[0]), D(cod[1]))}
    fs.book = {"deposits": Delta(D(deposits[0]), D(deposits[1])),
               "advances": Delta(D("7673600000"), D("7636182989")),
               "casa_ratio": Delta(D(casa[0]), D(casa[1]))}
    fs.bridge = {"mode": "preceding_period", "total": D(0), "volume": D("60551"),
                 "rate": D("-23306"), "interaction": D(0), "top": []}
    return fs


# --- benchmark drift --------------------------------------------------------- #

def test_a_wide_gap_is_serious_and_carries_a_rate_change_draft():
    [f] = detectors.benchmark_drift(sheet(benchmarks=[gap()]))
    assert f.severity == "serious" and f.subject == "HMLN5"
    assert f.money_at_stake == D("107332") and f.money_basis == "per month"
    # A proposal is rounded the way a rate is quoted: 8.645 -> 8.65.
    assert f.action["type"] == "prepare_rate_change" and f.action["suggested"] == "8.65"
    assert len(f.action["note"]) <= 500
    assert "look more profitable than they are" in f.body


def test_deposit_gap_below_market_reads_as_under_credited():
    [f] = detectors.benchmark_drift(sheet(benchmarks=[gap(
        code="TDR03", name="TERM DEPOSIT - 3 MONTHS", side="LIABILITY", bench="7.5",
        market="8.32", gap_bp=-82, impact="-538716", tenor=91)]))
    assert "credited" in f.body and "less" in f.body and f.severity == "serious"


def test_small_gaps_and_behavioural_deposits_are_left_to_the_table():
    fs = sheet(benchmarks=[gap(gap_bp=-20, impact="-50000"),
                           gap(code="CAIBC", side="LIABILITY", gap_bp=-447, behavioural=True)])
    assert detectors.benchmark_drift(fs) == []


def test_money_alone_can_raise_a_small_gap():
    [f] = detectors.benchmark_drift(sheet(benchmarks=[gap(gap_bp=-32, impact="-580723")]))
    assert f.severity == "serious"                     # over ৳5 lakh a month


def test_an_old_market_point_lowers_the_severity_and_says_so():
    [f] = detectors.benchmark_drift(sheet(benchmarks=[gap(as_of=TODAY - timedelta(days=40))]))
    assert f.severity == "warning" and "confirm it before acting" in f.body


# --- market ------------------------------------------------------------------ #

def test_call_money_jump_in_a_week_is_serious():
    fs = sheet(market={"BB_CALL_ON": point("BB_CALL_ON", "9.40", week="8.79",
                                           week_as_of=date(2026, 9, 17), short="Call money O/N")})
    [f] = detectors.market_moves(fs)
    assert f.severity == "serious" and f.audience == "PUBLIC" and "up 61 bp" in f.title


def test_a_stale_series_raises_nothing():
    fs = sheet(market={"BB_CALL_ON": point("BB_CALL_ON", "9.40", week="8.79",
                                           week_as_of=date(2026, 9, 17), stale=True)})
    assert detectors.market_moves(fs) == []


def test_global_moves_inform_rather_than_alarm():
    fs = sheet(market={"US_FEDFUNDS": point("US_FEDFUNDS", "3.38", prev="3.88",
                                            prev_as_of=date(2026, 9, 23), category="global_rates")})
    [f] = detectors.market_moves(fs)
    assert f.severity == "warning"                     # would be serious for a taka rate


def test_a_policy_rate_change_is_the_headline():
    fs = sheet(market={"BB_POLICY": point("BB_POLICY", "10.00", prev="9.50",
                                          prev_as_of=date(2026, 7, 1), category="bd_policy")})
    found = detectors.run(fs, include_public=True, head_office=False)
    assert found[0].kind == "policy_rate" and found[0].severity == "critical"
    assert brief.headline(fs, found).startswith("Bangladesh Bank raised the policy rate by 50 bp")


# --- the book ------------------------------------------------------------------ #

def test_a_steady_book_raises_nothing():
    assert detectors.run(book(sheet()), include_public=False, head_office=False) == []


def test_margin_squeeze_and_dearer_deposits_are_priced():
    fs = book(sheet(), nim=("8.50", "8.90"), cod=("4.60", "4.27"))
    kinds = {f.kind: f for f in detectors.run(fs, include_public=False, head_office=False)}
    assert kinds["nim_compression"].severity == "serious"
    assert kinds["cof_rise"].severity == "serious"
    # 9,185,862,304 x 0.33% x 30/365
    assert abs(kinds["cof_rise"].money_at_stake - D("2491507")) < 50


def test_deposit_runoff():
    fs = book(sheet(), deposits=("8600000000", "9154243425"))
    [f] = [x for x in detectors.run(fs, include_public=False, head_office=False)
           if x.kind == "deposit_runoff"]
    assert f.severity == "serious" and f.money_at_stake == D("554243425")


def test_old_bank_data_is_flagged():
    fs = book(sheet())
    fs.business_date = TODAY - timedelta(days=4)
    [f] = detectors.data_freshness(fs)
    assert f.severity == "warning" and "4 days old" in f.title


def test_branch_drop_names_the_branch_by_placeholder():
    fs = book(sheet(), net=("900000", "1000000"))
    fs.branch_movers = [Segment("branch", "0101", D("-120000"), D("-100000"), D("-20000"),
                                D("400000"), D("280000"))]
    [f] = detectors.branch_movers(fs)
    assert f.title.startswith("{BR:0101}")
    assert fill_names(f.title, fs.names).startswith("Gulshan (0101)")


def test_findings_are_ranked_by_severity_then_money():
    fs = book(sheet(benchmarks=[gap(gap_bp=-60, impact="-310000"),
                                gap(code="X", gap_bp=-55, impact="-450000")]))
    found = detectors.run(fs, include_public=False, head_office=True)
    assert [f.subject for f in found] == ["X", "HMLN5"]


# --- the standard brief -------------------------------------------------------- #

def test_brief_leads_with_the_decisions_and_their_money():
    fs = book(sheet(benchmarks=[gap(), gap(code="TDR03", gap_bp=-82, impact="-538716")]))
    found = detectors.run(fs, include_public=False, head_office=True)
    b = brief.compose(fs, found)
    assert b["headline"] == "2 decisions for today: about ৳6.46 lakh a month rests on them"
    assert [d["subject"] for d in b["decisions"]] == ["TDR03", "HMLN5"]
    assert [s["key"] for s in b["sections"]] == ["book"]


def test_a_branch_users_brief_is_market_only():
    fs = sheet(scope_key="PUBLIC", market={"BB_CALL_ON": point("BB_CALL_ON", "8.79")})
    b = brief.compose(fs, [])
    assert [s["key"] for s in b["sections"]] == ["markets"]
    assert b["headline"] == "Markets steady: nothing needs a decision today"


# --- what a provider sees ------------------------------------------------------ #

def _vault() -> Vault:
    v = Vault()
    v.register([Entity("BR", "0101", "Gulshan (0101)", ("Gulshan", "0101"))])
    return v


def test_rendered_bank_text_has_tokens_and_blurred_amounts_only():
    fs = book(sheet(benchmarks=[gap()]), net=("900000", "1000000"))
    fs.branch_movers = [Segment("branch", "0101", D("-120000"), D("-100000"), D("-20000"),
                                D("400000"), D("280000"))]
    found = detectors.run(fs, include_public=False, head_office=True)
    v = _vault()
    out = render(fs, found, v, FieldPolicy())
    assert "Gulshan" not in out.bank and "0101" not in out.bank
    assert v.token("BR", "0101") in out.bank
    assert "114050125" not in out.bank and "11.4 cr" in out.bank       # 3 s.f. in crore
    assert '"HOME LOAN - 5 YEARS"' in out.bank                           # product names pass
    assert dlp.scan([("bank", out.bank)], v.raw_spellings(), numeric_known={"0101"}) == []


def test_product_names_become_tokens_when_the_policy_says_so():
    fs = book(sheet(benchmarks=[gap()]))
    found = detectors.run(fs, include_public=False, head_office=True)
    v = _vault()
    out = render(fs, found, v, FieldPolicy.from_dict({"actions": {"product_name": "token"}}))
    assert "HOME LOAN" not in out.bank and v.token("PRD", "HMLN5") in out.bank


def test_market_goes_in_the_public_section_and_a_scope_without_data_sends_no_bank_text():
    fs = sheet(scope_key="PUBLIC", market={"BB_CALL_ON": point("BB_CALL_ON", "8.79",
                                                                 short="Call money O/N")})
    out = render(fs, [], _vault(), FieldPolicy())
    assert out.bank == "" and "Call money O/N: 8.79%" in out.public


# --- who sees what -------------------------------------------------------------- #

def test_visibility_follows_scope():
    assert visible_keys(ScopeLevel.HO, None) == ["HO", "PUBLIC"]
    assert visible_keys(ScopeLevel.DIVISION, 3) == ["DIV:3", "PUBLIC"]
    assert visible_keys(ScopeLevel.DISTRICT, 12) == ["PUBLIC"]
    assert visible_keys(ScopeLevel.BRANCH, 5) == ["PUBLIC"]
    assert scope_key_for(ScopeLevel.BRANCH, 5) == "PUBLIC"

"""Public data: Bangladesh Bank's tables, macro responses, peer pastes, matching.

The fixtures are trimmed copies of the real pages, saved 2026-10-01.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal as D
from pathlib import Path

import httpx

from app.ai.market import sources
from app.ai.permissions import AI_GRANTS
from app.ai.presets import agentic_default
from app.ai.public import match, parse

FIX = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIX / name).read_text()


# --- Bangladesh Bank's tables ------------------------------------------------------ #

def test_ranges_and_blanks():
    assert parse.rate_range("7.00-9.25") == (D("7.00"), D("9.25"))
    assert parse.rate_range("3.00") == (D("3.00"), D("3.00"))
    assert parse.rate_range("") is None
    assert parse.rate_range("- - -") is None
    # A misread cell (an amount, a year) is not a rate.
    assert parse.rate_range("2026") is None


def test_deposit_table_reads_every_bank_with_its_group():
    t = parse.deposit_table(_read("bb_deposit.html"))
    assert t.month == date(2026, 8, 1) and not t.warnings
    banks = {r.bank: r.group for r in t.rows}
    assert len(banks) == 61
    assert banks["AGRANI"] == "SCB" and banks["NRBBL"] == "PCB" and banks["HSBC"] == "FB"
    agrani = {r.product: (r.low, r.high) for r in t.rows if r.bank == "AGRANI"}
    assert agrani["savings"] == (D("3.00"), D("3.00"))
    assert agrani["fd_1y"] == (D("8.50"), D("8.50"))
    basic_fd = next(r for r in t.rows if r.bank == "BASIC" and r.product == "fd_3m")
    assert (basic_fd.low, basic_fd.high) == (D("7.00"), D("9.25"))


def test_lending_table_reads_only_the_first_sub_category():
    t = parse.lending_table(_read("bb_lending.html"))
    assert t.month == date(2026, 8, 1)
    agrani = [r for r in t.rows if r.bank == "AGRANI"]
    # SC.2 rows (special schemes) would give AGRANI a second "term_large".
    assert len([r for r in agrani if r.product == "term_large"]) == 1
    assert next(r for r in agrani if r.product == "agriculture").low == D("12.40")


def test_industry_rates_by_month_with_gaps():
    rows = parse.industry_rates(_read("bb_intrate.html"))
    assert rows[0].month_end == date(2025, 1, 31)
    jul = next(r for r in rows if r.month_end == date(2026, 7, 31))
    assert (jul.deposit_rate, jul.advance_rate, jul.spread) == (D("6.21"), D("11.81"), D("5.60"))
    aug = rows[-1]
    assert aug.month_end == date(2026, 8, 31) and aug.deposit_rate is None
    assert aug.call_money == D("9.34")


def test_policy_box_and_last_mpc():
    pr = parse.policy_rates(_read("bb_home.txt"))
    assert (pr.repo, pr.slf, pr.sdf, pr.bank_rate) == (D("9.50"), D("11.00"), D("7.50"), D("4.00"))
    assert pr.as_of == date(2026, 2, 15) and pr.last_mpc == date(2026, 9, 23)


def test_call_money_history_takes_overnight_and_seven_day():
    days = parse.call_money_history(_read("bb_callmoney_hist.html"))
    last = days[-1]
    assert last.day == date(2026, 9, 30)
    assert (last.overnight, last.volume, last.short_notice_7d) == (D("8.45"), D("2654.35"), D("8.50"))
    assert days == sorted(days, key=lambda d: d.day)


def test_bb_html_stops_on_a_challenge():
    t = httpx.MockTransport(lambda req: httpx.Response(200, text="testing whether you are a "
                                                                  "human visitor"))
    try:
        sources.bb_html("https://www.bb.org.bd/x", transport=t)
    except sources.Blocked:
        pass
    else:
        raise AssertionError("a CAPTCHA must stop collection")


# --- macro ------------------------------------------------------------------------- #

def test_worldbank_and_imf_shapes():
    wb = [{"page": 1}, [{"date": "2025", "value": 8.77}, {"date": "2024", "value": None},
                        {"date": "2023", "value": 9.88}]]
    assert parse.worldbank(wb) == [(2023, D("9.88")), (2025, D("8.77"))]
    imf = {"values": {"PCPIPCH": {"SDN": {"2026": 99}, "BGD": {"2026": 9.2, "2027": 6}}}}
    assert parse.imf(imf, "PCPIPCH") == [(2026, D("9.2")), (2027, D("6"))]
    assert parse.imf({"values": {}}, "PCPIPCH") == []


# --- peer financials ---------------------------------------------------------------- #

def test_peer_paste_wide_and_long():
    figs, warn = parse.peer_financials("bank\tperiod\tNIM\tCASA\tfoo\nbrac\tQ2 2026\t4.1\t45%\tx\n")
    assert [(f.bank, f.period_end, f.metric, f.value) for f in figs] == [
        ("BRAC", date(2026, 6, 30), "nim", D("4.1")), ("BRAC", date(2026, 6, 30), "casa", D("45"))]
    assert any("foo" in w for w in warn)
    figs, _ = parse.peer_financials("bank,period,metric,value\nEBL,2026-06-30,cof,6.2\n")
    assert figs[0].metric == "cost_of_funds"
    assert parse.peer_financials("nonsense\n1,2")[0] == []


# --- matching our products to the tables ----------------------------------------------- #

def test_peer_keys_follow_name_nature_and_term():
    k = match.peer_key
    assert k("TERM DEPOSIT - 3 MONTHS", "LIABILITY", "TIME") == ("deposit", "fd_3m")
    assert k("TERM DEPOSIT - 1 YEAR", "LIABILITY", "TIME") == ("deposit", "fd_1y")
    assert k("TERM DEPOSIT - 3 YEARS", "LIABILITY", "TIME") == ("deposit", "fd_3y")
    assert k("SAVINGS ACCOUNT - STANDARD", "LIABILITY", "DEMAND") == ("deposit", "savings")
    assert k("SPECIAL SAVINGS (NOTICE)", "LIABILITY", "DEMAND") == ("deposit", "snd_lt1cr")
    assert k("CURRENT ACCOUNT", "LIABILITY", "DEMAND") is None
    assert k("HOME LOAN - 5 YEARS", "ASSET", None) == ("lending", "housing")
    assert k("SME TIME LOAN REVOLVING", "ASSET", None) == ("lending", "wc_small")
    assert k("TERM LOAN - SME", "ASSET", None) == ("lending", "term_small")
    assert k("LOAN AGAINST TRUST RECEIPT (LATR)", "ASSET", None) == ("lending", "trade")
    assert k("AUTO LOAN", "ASSET", None) == ("lending", "consumer")
    assert k("CORPORATE OVERDRAFT", "ASSET", None) == ("lending", "wc_large")


def test_standing_ranks_deposits_high_first_loans_low_first():
    vals = {"A": D("9.0"), "B": D("8.0"), "C": D("7.0"), "ME": D("8.5")}
    dep = match.standing(vals, "ME", higher_first=True)
    assert dep["rank"] == 2 and dep["median"] == D("8.25") and dep["banks"] == 4
    loan = match.standing(vals, "ME", higher_first=False)
    assert loan["rank"] == 3
    assert match.standing({}, "ME", higher_first=True)["median"] is None


# --- capability and access ----------------------------------------------------------- #

def test_agentic_default_by_model():
    assert agentic_default("anthropic", "claude-sonnet-5-5")
    assert agentic_default("openai_compat", "gpt-5-mini")
    assert agentic_default("openai_compat", "gemini-2.5-flash")
    assert not agentic_default("openai_compat", "qwen3:8b")
    assert not agentic_default("openai_compat", "llama3.1:8b")
    assert agentic_default("openai_compat", "llama-3.3-70b-versatile")


def test_branch_users_may_ask():
    assert "AI_CHAT" in AI_GRANTS["VIEWER"]
    assert "AI_ADMIN" not in AI_GRANTS["VIEWER"]


def test_retention_month_cut():
    from app.ai.market.service import months_back
    assert months_back(date(2026, 10, 1), 36) == date(2023, 10, 1)
    assert months_back(date(2026, 1, 15), 1) == date(2025, 12, 1)
    assert months_back(date(2026, 3, 31), 14) == date(2025, 1, 1)

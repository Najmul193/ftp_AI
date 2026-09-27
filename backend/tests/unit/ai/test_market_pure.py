"""Market parsing and inference, against the real Bangladesh Bank page layouts."""

from __future__ import annotations

from datetime import date
from decimal import Decimal as D

from app.ai.market import news_tags, paste
from app.ai.market.tenor import curve_rate, infer_tenor_days

# As a browser copies the call money page: tab-separated cells.
CALL_MONEY = """Statement of Interbank Money Market Transactions
24 September, 2026
Product\tMaturity\tAmount (Crore Taka)\tInterest rate (%)\t\t\tNumber of Deals
\t\t\tHighest\tLowest\tAverage\t
Overnight\t1 Day(/s)\t2811.39\t11.00\t8.45\t8.79\t51
Short Notice\t4 Day(/s)\t50.00\t8.90\t8.90\t8.90\t1
Short Notice\t7 Day(/s)\t763.00\t9.50\t8.30\t8.89\t6
Short Notice\t14 Day(/s)\t300.89\t12.00\t8.50\t9.29\t3
Operation Time: 10:00am-04:15pm"""

# The same page when each cell lands on its own line.
CALL_MONEY_CELLS = """24 September, 2026
Overnight
1 Day(/s)
2811.39
11.00
8.45
8.79
51"""

REF_RATES = """Dhaka Overnight Money Market Rate (DOMMR)
Product\tAmount (Crore Taka)\tDOMMR (%)\tNumber of Deals
27 September, 2026
Overnight\t2721.00\t8.79\t46
1W\t7181.35\t8.93\t78
1M\t260.00\t9.42\t5
3M\t216.60\t9.90\t5
Bangladesh Overnight Financing Rate (BOFR)
Product\tAmount (Crore Taka)\tBOFR (%)\tNumber of Deals
27 September, 2026
Overnight\t2735.53\t8.96\t21
1W\t12639.72\t9.00\t98"""

AUCTIONS = """Primary Issue/Auction of 91-day, 182-day & 364-day T-Bills, and 2-yr, 5-yr, 10-yr, 15-yr & 20-yr Treasury Bonds
18/09/2025\tBD0901417255\t14 days\t14 days T.Bill\t87\t12276.74\t9.9449-12.6194\t53\t5000.00\t4980.8762\t9.9449-10.0500\t99.6175\t10.0500\t—
21/09/2026\tBD0909112262\t91 days\t91 days T.Bill\t378\t9595.16\t8.1886-10.5599\t325\t3000.00\t2939.49\t8.1886-8.3198\t97.9830\t8.3198\t—
14/09/2026\tBD0909112001\t91 days\t91 days T.Bill\t300\t9000.00\t8.2000-10.0000\t300\t3000.00\t2939.00\t8.2000-8.4000\t97.9000\t8.4000\t—
21/09/2026\tBD0918212277\t182 days\t182 days T.Bill\t209\t5379.53\t8.2399-10.6599\t174\t2500.00\t2399.83\t8.2399-8.4498\t95.9932\t8.4498\t—
21/09/2026\tBD0936412271\t364 days\t364 days T.Bill\t171\t4698.63\t8.2800-10.7600\t133\t2000.00\t1845.23\t8.2800-8.4700\t92.2616\t8.4700\t—
02/09/2026\tBD0929441204 (Re-issuance: 2.73 Yr.)\t2yr\t20yr T.Bond\t339\t9773.16\t8.7280-11.7100\t257\t3500.00\t3689.28\t8.7280-8.8685\t105.4081\t8.8685\t8.75
02/09/2026\tBD0929261032 (Re-issuance: 2.35 Yr.)\t3yr\t3yr FRT.Bond\t44\t1109.37\t8.8899-11.8600\t21\t500.00\t511.79\t8.8899-9.6948\t102.3581\t9.6948\t9.73
09/09/2026\tBD0932851100 (Re-issuance: 5.71 Yr.)\t5yr\t10yr T.Bond\t335\t10051.93\t8.5494-11.0600\t203\t3000.00\t2919.13\t8.5494-8.6450\t97.3044\t8.6450\t8.53
16/09/2026\tBD0937901157 (Re-issuance: 10.78 Yr.)\t10yr\t15yr T.Bond\t248\t8948.57\t8.3899-11.0600\t88\t2500.00\t2554.96\t8.3899-8.5400\t100.3528\t8.5400\t8.39
23/09/2026\tBD0945081208 (Re-issuance: 18.93 Yr.)\t20yr\t20yr T.Bond\t127\t2424.57\t8.9780-11.2500\t82\t1000.00\t1097.48\t8.9780-9.3480\t109.7480\t9.3480\t9.35"""


def _by_code(r):
    return {i.code: i for i in r.items}


def test_call_money_page():
    r = paste.parse(CALL_MONEY)
    assert r.kind == "call_money" and r.page_date == date(2026, 9, 24)
    got = _by_code(r)
    assert got["BB_CALL_ON"].value == D("8.79")
    assert got["BB_CALL_ON_VOL"].value == D("2811.39")
    assert got["BB_SN_7D"].value == D("8.89")
    assert all(i.obs_date == date(2026, 9, 24) for i in r.items)
    assert r.warnings == []


def test_call_money_one_cell_per_line():
    got = _by_code(paste.parse(CALL_MONEY_CELLS))
    assert got["BB_CALL_ON"].value == D("8.79")


def test_reference_rates_page():
    r = paste.parse(REF_RATES)
    assert r.kind == "ref_rates" and r.page_date == date(2026, 9, 27)
    got = {c: i.value for c, i in _by_code(r).items()}
    assert got == {"BB_DOMMR_ON": D("8.79"), "BB_DOMMR_1W": D("8.93"), "BB_DOMMR_1M": D("9.42"),
                   "BB_DOMMR_3M": D("9.90"), "BB_BOFR_ON": D("8.96"), "BB_BOFR_1W": D("9.00")}


def test_auctions_use_remaining_maturity_latest_date_and_skip_floaters():
    r = paste.parse(AUCTIONS)
    assert r.kind == "auctions"
    got = _by_code(r)
    # Latest 91-day auction wins over the earlier one in the same paste.
    assert got["BB_TBILL_91"].value == D("8.3198") and got["BB_TBILL_91"].obs_date == date(2026, 9, 21)
    assert got["BB_TBILL_182"].value == D("8.4498")
    assert got["BB_TBILL_364"].value == D("8.4700")
    # A re-issued 20-year bond with ~2 years left is the 2-year point.
    assert got["BB_TBOND_2Y"].value == D("8.8685")
    assert got["BB_TBOND_5Y"].value == D("8.6450")
    assert got["BB_TBOND_10Y"].value == D("8.5400")
    assert got["BB_TBOND_20Y"].value == D("9.3480")
    # 14-day bills and floating-rate bonds are not curve points.
    assert not any("14" in c for c in got) and "BB_TBOND_3Y" not in got
    assert r.warnings == []
    assert r.page_date == date(2026, 9, 23)


# What Ctrl+A picks up around the table: the site menu names every page.
NAV = """Bangladesh Bank
Home About us Monetary Policy
Inter Bank Money Market
Money Market Reference Rate
Dhaka Overnight Money Market Rate DOMMR BOFR
Treasury Bill/Bond T.Bill T.Bond Auction
Notice 12 August, 2025 Holiday on account of National Mourning Day
"""


def test_whole_page_copy_with_menus_reads_the_right_table():
    r = paste.parse(NAV + CALL_MONEY + "\nContact location")
    got = _by_code(r)
    assert set(got) == {"BB_CALL_ON", "BB_CALL_ON_VOL", "BB_SN_7D"}
    assert got["BB_CALL_ON"].value == D("8.79")
    # The table's own date, not the first date on the page (a menu notice).
    assert got["BB_CALL_ON"].obs_date == date(2026, 9, 24)

    got = _by_code(paste.parse(NAV + REF_RATES))
    assert set(got) == {"BB_DOMMR_ON", "BB_DOMMR_1W", "BB_DOMMR_1M", "BB_DOMMR_3M",
                        "BB_BOFR_ON", "BB_BOFR_1W"}
    assert got["BB_DOMMR_ON"].obs_date == date(2026, 9, 27)

    got = _by_code(paste.parse(NAV + AUCTIONS))
    assert "BB_TBILL_91" in got and not any(c.startswith(("BB_CALL", "BB_DOMMR")) for c in got)


def test_auctions_one_cell_per_line():
    # How a browser's whole-page copy often lays a table out.
    cells = NAV + "\n".join(AUCTIONS.replace("\t", "\n").splitlines())
    got = _by_code(paste.parse(cells))
    assert got["BB_TBILL_91"].value == D("8.3198")
    assert got["BB_TBOND_2Y"].value == D("8.8685")
    assert got["BB_TBOND_20Y"].obs_date == date(2026, 9, 23)


def test_call_money_row_is_never_read_as_a_reference_rate():
    # The overnight row has five numbers; a reference-rate row has three.
    r = paste.parse("Dhaka Overnight Money Market Rate (DOMMR)\n" + CALL_MONEY)
    assert "BB_DOMMR_ON" not in _by_code(r)


def test_unrecognised_paste():
    r = paste.parse("hello world")
    assert r.kind == "unknown" and r.warnings and not r.items


def test_implausible_rate_rejected():
    bad = "24 September, 2026\nOvernight\t1 Day(/s)\t2811.39\t11.00\t8.45\t88.79\t51"
    r = paste.parse(bad)
    assert "BB_CALL_ON" not in _by_code(r) and r.warnings


# --- tenor ------------------------------------------------------------------ #

def test_tenor_from_name_and_nature():
    assert infer_tenor_days("TERM DEPOSIT - 3 MONTHS", "LIABILITY", "TIME")[0] == 91
    assert infer_tenor_days("TERM DEPOSIT - 6 MONTHS", "LIABILITY", "TIME")[0] == 182
    assert infer_tenor_days("TERM DEPOSIT - 1 YEAR", "LIABILITY", "TIME")[0] == 364
    assert infer_tenor_days("TERM DEPOSIT - 3 YEARS", "LIABILITY", "TIME")[0] == 1095
    assert infer_tenor_days("HOME LOAN - 5 YEARS", "ASSET", None)[0] == 1825
    assert infer_tenor_days("SAVINGS ACCOUNT - STANDARD", "LIABILITY", "DEMAND")[0] == 364
    assert infer_tenor_days("CORPORATE OVERDRAFT", "ASSET", None)[0] == 91


def test_curve_interpolation_and_flat_ends():
    pts = [(1, D("8.79")), (91, D("8.32")), (364, D("8.47")), (1825, D("8.65"))]
    assert curve_rate(pts, 91)[0] == D("8.32")
    r, basis = curve_rate(pts, 182)
    assert D("8.32") < r < D("8.47") and "between" in basis
    assert curve_rate(pts, 7300)[0] == D("8.65")
    assert curve_rate([], 91) is None


# --- news tags -------------------------------------------------------------- #

def test_news_tagging_and_signal():
    t = news_tags.tag("Call money rate rises to 9.1% as liquidity tightens", source="The Daily Star")
    assert "liquidity" in t.tags and t.rate_signal == 1 and t.relevance >= 4
    t = news_tags.tag("Bangladesh Central Bank Holds Rate as Risks Linger - Bloomberg")
    assert "policy_rate" in t.tags and t.rate_signal == 0
    t = news_tags.tag("T-bill yields fall for third week", source="TBS")
    assert "govt_securities" in t.tags and t.rate_signal == -1
    assert news_tags.tag("Cricket team wins series").relevance == 0

"""The market rate explorer's pure parts: the bank directory, product mapping,
the market_rates plan, and other banks' rate moves."""

from __future__ import annotations

from datetime import date
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace

from app.ai.copilot.catalog import parse_plan
from app.ai.insights import detectors
from app.ai.insights.facts import FactSheet
from app.ai.public import banks, parse
from app.ai.public.service import category_of

FIX = Path(__file__).parent / "fixtures"


def test_every_code_in_the_table_has_a_full_name():
    codes = {r.bank for r in parse.deposit_table((FIX / "bb_deposit.html").read_text()).rows}
    assert len(codes) == 61
    missing = [c for c in codes if c not in banks.BY_CODE]
    assert missing == []
    assert banks.BY_CODE["THE CITY"].name == "City Bank PLC"
    assert banks.BY_CODE["NRBBL"].group == "PCB" and banks.BY_CODE["HSBC"].group == "FB"


def test_people_name_banks_their_own_way():
    r = lambda q: (banks.resolve_bank(q) or SimpleNamespace(code=None)).code  # noqa: E731
    assert r("city") == "THE CITY" and r("City Bank") == "THE CITY"
    assert r("Eastern") == "EBL" and r("dbbl") == "DUTCH-BANGLA"
    assert r("nrb bank") == "NRBBL" and r("NRBC") == "NRBCBL"
    assert r("standard chartered") == "STAN.CHART" and r("mtb") == "MUTUAL TRUST"
    assert r("no such bank anywhere") is None


def test_bank_names_follow_bangladesh_banks_own_list():
    html = ('<select name="select_bank"><option value="">Select Bank</option>'
            '<option value="16">City Bank PLC (renamed)</option></select>')
    names = banks.parse_bank_list(html)
    assert names == {"16": "City Bank PLC (renamed)"}
    assert banks.with_names(names)["THE CITY"].name == "City Bank PLC (renamed)"


def _prod(code, name, side="LIABILITY", nature="TIME"):
    return SimpleNamespace(product_code=code, short_name=name, side=SimpleNamespace(value=side),
                           liability_nature=SimpleNamespace(value=nature) if nature else None)


def test_head_office_mapping_beats_the_guess():
    p = _prod("NEWFD", "SUPER SAVER FIXED 13 MONTHS")
    assert category_of(p, {})[1] == "auto"
    assert category_of(p, {"NEWFD": "deposit:fd_1y"}) == (("deposit", "fd_1y"), "set")
    assert category_of(p, {"NEWFD": "none"}) == (None, "set")
    # A stale or mistyped override falls back to the guess rather than breaking.
    assert category_of(p, {"NEWFD": "deposit:nonsense"})[1] == "auto"


def test_market_rates_plan():
    p = parse_plan({"tool": "market_rates", "banks": ["City", "EBL"], "products": ["1 year FD"],
                    "group": "pcb", "limit": 5, "include_ours": False})
    assert (p.peer_banks, p.categories, p.peer_set, p.limit, p.include_ours) == \
        (("City", "EBL"), ("1 year FD",), "pcb", 5, False)
    assert parse_plan({"tool": "market_rates"}).include_ours is True


def test_a_competitor_paying_more_for_deposits_is_news():
    fs = FactSheet(scope_key="PUBLIC", scope_label="", today=date(2026, 10, 1))
    fs.rate_moves = [
        {"book": "deposit", "product": "fd_1y", "label": "Fixed deposit 1–2 years", "code": "THE CITY",
         "name": "City Bank PLC", "from": D("8.00"), "to": D("8.75"), "change_bp": 75,
         "month": date(2026, 9, 1)},
        {"book": "lending", "product": "housing", "label": "Housing loan", "code": "EBL",
         "name": "Eastern Bank PLC", "from": D("10"), "to": D("10.3"), "change_bp": 30,
         "month": date(2026, 9, 1)},
    ]
    [f] = detectors.competitor_moves(fs)
    assert f.audience == "PUBLIC" and f.severity == "warning" and "+75 bp" in f.title
    assert len(f.kind) <= 40 and len(f.subject) <= 60


def test_a_posted_range_is_read_the_way_a_customer_shops():
    from app.ai.public.explorer import value
    nrb_fd = (D("2.75"), D("10.25"), D("6.50"))            # BB: "2.75-10.25"
    assert value(nrb_fd, "deposit", "best") == D("10.25")    # a depositor sees the best rate
    assert value(nrb_fd, "lending", "best") == D("2.75")     # a borrower, the lowest
    assert value(nrb_fd, "deposit", "typical") == D("6.50")

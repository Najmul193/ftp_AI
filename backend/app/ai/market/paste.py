"""Read Bangladesh Bank rates from text a person copied off the BB website.

bb.org.bd answers automated clients with a CAPTCHA, so nothing here fetches
it. Treasury opens the page in a browser, selects all, copies, and pastes;
this turns that text into proposed observations, which a person confirms
before anything is saved.

Three pages are recognised, by their headings and row labels:

    Call money market        "Overnight  1 Day(/s)  2811.39  11.00  8.45  8.79  51"
    Money market ref. rates  DOMMR / BOFR: "Overnight  2721.00  8.79  46"
    Treasury auctions        "21/09/2026  BD0909112262  91 days  91 days T.Bill ...
                              8.1886-8.3198  97.9830  8.3198  —"

Copying from a browser gives tab-separated cells, but people also paste
from PDFs, e-mails and spreadsheets, so rows are read by their shape --
labels and runs of numbers -- never by column position alone. Where a value
can be cross-checked (an auction's cut-off yield is the top of the accepted
range) it is.

PURE: no I/O.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

_MONTHS = ("january february march april may june july august september "
           "october november december").split()
_LONG_DATE = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(" + "|".join(_MONTHS)
                        + r"|" + "|".join(m[:3] for m in _MONTHS) + r")[a-z]*\.?,?\s+(\d{4})\b", re.I)
_SLASH_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
_NUM = re.compile(r"(?<![\w./-])-?\d[\d,]*(?:\.\d+)?(?![\w/])")
_RANGE = re.compile(r"(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)")
_ISIN = re.compile(r"\bBD\d{10}\b")
_REISSUE = re.compile(r"\((?:re-?issuance)[^)]*\)", re.I)
_ROW_START = re.compile(r"(?=\b\d{1,2}/\d{1,2}/\d{4}\b\s*BD\d{10}\b)")
_TENOR = re.compile(r"(?<![\d.])(\d+)\s*(days?|yrs?|years?)\b", re.I)

#: Plausible bounds for a taka interest rate, in percent.
_RATE_MIN, _RATE_MAX = Decimal("0.01"), Decimal("40")


@dataclass
class Proposed:
    code: str
    obs_date: date | None
    value: Decimal
    #: Where in the paste it came from, shown to the person confirming.
    evidence: str


@dataclass
class ParseResult:
    kind: str                                   # call_money | ref_rates | auctions | unknown
    page_date: date | None
    items: list[Proposed] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _dec(s: str) -> Decimal | None:
    try:
        return Decimal(s.replace(",", ""))
    except InvalidOperation:
        return None


def _long_date(text: str) -> date | None:
    m = _LONG_DATE.search(text)
    if not m:
        return None
    mon = next(i for i, name in enumerate(_MONTHS, 1) if name.startswith(m.group(2).lower()[:3]))
    try:
        return date(int(m.group(3)), mon, int(m.group(1)))
    except ValueError:
        return None


def _slash_date(s: str) -> date | None:
    m = _SLASH_DATE.search(s)
    if not m:
        return None
    try:
        return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1))).date()
    except ValueError:
        return None


def _numbers(line: str) -> list[Decimal]:
    """Stand-alone numbers only: not the parts of a date, range or ISIN."""
    clean = _RANGE.sub(" ", _SLASH_DATE.sub(" ", _ISIN.sub(" ", line)))
    out = []
    for m in _NUM.finditer(clean):
        v = _dec(m.group(0))
        if v is not None:
            out.append(v)
    return out


def _is_rate(v: Decimal) -> bool:
    return _RATE_MIN <= v <= _RATE_MAX


def _lines(text: str) -> list[str]:
    """Rows, with cells a browser split across lines re-joined.

    A copied table sometimes arrives one cell per line. A line holding only a
    number is glued to the line before it, so each row reads as one line.
    """
    raw = [ln.strip() for ln in text.replace("\r", "\n").split("\n")]
    rows: list[str] = []
    for ln in raw:
        if not ln:
            continue
        if rows and (re.fullmatch(r"[\d.,\s%—-]+", ln)
                     or re.fullmatch(r"\d+\s*(?:days?|yrs?|years?)(?:\(/s\))?\.?", ln, re.I)):
            rows[-1] += "\t" + ln
        else:
            rows.append(ln)
    return rows


# --------------------------------------------------------------------------- #

def parse(text: str) -> ParseResult:
    """Run every reader and keep what each finds.

    A whole-page copy includes the site's menus, which name every page
    ("Money Market Reference Rate", "T.Bill"), so the page cannot be told from
    its keywords. Each reader is strict about the shape of its own rows
    instead, which is what keeps one page's figures from being read as
    another's -- the call money overnight row has five numbers, a reference
    rate row three, an auction row a date and an ISIN.
    """
    found = [r for r in (_call_money(text), _ref_rates(text), _auctions(text)) if r.items]
    if not found:
        r = ParseResult("unknown", None)
        r.warnings.append("No Bangladesh Bank call money, reference rate or treasury auction "
                          "rows were found in the pasted text.")
        return r
    if len(found) == 1:
        return found[0]
    merged = ParseResult("+".join(r.kind for r in found),
                         max((r.page_date for r in found if r.page_date), default=None))
    for r in found:
        merged.items += r.items
        merged.warnings += r.warnings
    return merged


def _call_money(text: str) -> ParseResult:
    res = ParseResult("call_money", None)
    day: date | None = None
    for row in _lines(text):
        low = row.lower()
        day = _long_date(row) or day
        nums = _numbers(row)
        if low.startswith("overnight") and len(nums) >= 5:
            res.page_date = res.page_date or day
            # ... amount, highest, lowest, average, deals
            amount, hi, lo, avg = nums[-5], nums[-4], nums[-3], nums[-2]
            if not (_is_rate(avg) and lo <= avg <= hi):
                res.warnings.append(f"Overnight row did not read cleanly: {row!r}")
                continue
            res.items.append(Proposed("BB_CALL_ON", day, avg,
                                      f"Overnight weighted average {avg}% (range {lo}–{hi}%)"))
            res.items.append(Proposed("BB_CALL_ON_VOL", day, amount,
                                      f"Overnight volume {amount} crore"))
        elif low.startswith("short notice") and len(nums) >= 5:
            tenor = _TENOR.search(row)
            if tenor and int(tenor.group(1)) == 7 and tenor.group(2).lower().startswith("day"):
                avg, lo, hi = nums[-2], nums[-3], nums[-4]
                if _is_rate(avg) and lo <= avg <= hi:
                    res.items.append(Proposed("BB_SN_7D", day, avg,
                                              f"Short notice 7 days weighted average {avg}%"))
    return res


def _ref_rates(text: str) -> ParseResult:
    res = ParseResult("ref_rates", None)
    table: str | None = None
    day: date | None = None
    names = {"overnight": "ON", "1w": "1W", "1m": "1M", "3m": "3M"}
    wanted = {"BB_DOMMR_ON", "BB_DOMMR_1W", "BB_DOMMR_1M", "BB_DOMMR_3M", "BB_BOFR_ON", "BB_BOFR_1W"}
    for row in _lines(text):
        low = row.lower()
        # A table heading, not a menu link: the heading carries the acronym in
        # brackets ("... Rate (DOMMR)") or the column header ("DOMMR (%)").
        if "(dommr)" in low or "dommr (%)" in low:
            table = "DOMMR"
        elif "(bofr)" in low or "bofr (%)" in low:
            table = "BOFR"
        day = _long_date(row) or day
        if table is None:
            continue
        label = low.split()[0] if low.split() else ""
        nums = _numbers(row)
        if label in names and len(nums) == 3:        # amount, rate, deals
            rate = nums[1]
            code = f"BB_{table}_{names[label]}"
            if code not in wanted or any(i.code == code for i in res.items):
                continue
            if not _is_rate(rate):
                res.warnings.append(f"{table} {label} rate {rate} is out of range")
                continue
            res.page_date = res.page_date or day
            res.items.append(Proposed(code, day, rate, f"{table} {names[label]} {rate}%"))
    return res


_BILLS = {91: "BB_TBILL_91", 182: "BB_TBILL_182", 364: "BB_TBILL_364"}
_BONDS = {2: "BB_TBOND_2Y", 5: "BB_TBOND_5Y", 10: "BB_TBOND_10Y", 15: "BB_TBOND_15Y",
          20: "BB_TBOND_20Y"}


def _auctions(text: str) -> ParseResult:
    res = ParseResult("auctions", None)
    latest: dict[str, Proposed] = {}
    # A row starts with its issue date followed by an ISIN. Rebuilding rows on
    # that boundary works whether cells arrived tab-separated or one per line.
    flat = "\t".join(ln.strip() for ln in text.splitlines() if ln.strip())
    for row in _ROW_START.split(flat):
        if not _SLASH_DATE.search(row) or not _ISIN.search(row):
            continue
        issued = _slash_date(row)
        body = _REISSUE.sub(" ", row)
        # Remaining maturity comes before the instrument's name in the row:
        # a re-issued 20-year bond with two years left prices as a 2-year.
        after_isin = body[_ISIN.search(body).end():]
        tenor = _TENOR.search(after_isin)
        if tenor is None:
            continue
        n, unit = int(tenor.group(1)), tenor.group(2).lower()
        if "frt" in body.lower():
            continue                          # floating-rate bonds are not on the curve
        code = _BILLS.get(n) if unit.startswith("day") else _BONDS.get(n)
        if code is None:
            continue
        ranges = _RANGE.findall(body)
        if len(ranges) < 2:
            res.warnings.append(f"Could not find the accepted yield range: {row[:80]!r}")
            continue
        cut_off = Decimal(ranges[1][1])       # top of the accepted range
        stand_alone = _numbers(body)
        if cut_off not in stand_alone:
            res.warnings.append(f"{code}: cut-off {cut_off}% taken from the accepted range; "
                                f"not confirmed by the cut-off column")
        if not _is_rate(cut_off):
            continue
        p = Proposed(code, issued, cut_off,
                     f"{tenor.group(0)} auction on {issued:%d %b %Y}: cut-off {cut_off}%")
        prev = latest.get(code)
        if prev is None or (issued and prev.obs_date and issued > prev.obs_date):
            latest[code] = p
    res.items = list(latest.values())
    res.page_date = max((i.obs_date for i in res.items if i.obs_date), default=None)
    return res

"""Read public tables into plain rows.

- Bangladesh Bank's bank-by-bank deposit and lending rate tables (monthly).
- Its interest-rate page: the industry's weighted average deposit and lending
  rates and spread, by month.
- The policy-rate box on its home page, and the date of the last MPC meeting.
- World Bank and IMF responses.
- Peer banks' quarterly figures as head office pastes them.

Bangladesh Bank prints a range ("7.00-9.25") where a bank posts more than
one rate for a product; a single rate is a range of one.

PURE: no I/O.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

MONTHS = {m: i for i, m in enumerate(
    ("january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"), start=1)}

#: The group headers Bangladesh Bank prints above each block of banks.
GROUPS = {"SCBS": "SCB", "DFIS": "DFI", "SBS": "DFI", "PCBS": "PCB", "FBS": "FB", "FCBS": "FB"}

#: Deposit table columns, left to right after the bank's name.
DEPOSIT_PRODUCTS = ("savings", "snd_lt1cr", "snd_1_25cr", "snd_25_50cr", "snd_50_100cr",
                    "snd_100cr", "fd_3m", "fd_6m", "fd_1y", "fd_2y", "fd_3y")
#: Lending table columns after the bank and its sub-category.
LENDING_PRODUCTS = ("agriculture", "term_large", "term_small", "wc_large", "wc_small", "export",
                    "trade", "housing", "consumer", "card", "nbfi", "others")

PRODUCT_LABELS = {
    "savings": "Savings deposit", "snd_lt1cr": "SND under ৳1 crore",
    "snd_1_25cr": "SND ৳1–25 crore", "snd_25_50cr": "SND ৳25–50 crore",
    "snd_50_100cr": "SND ৳50–100 crore", "snd_100cr": "SND ৳100 crore+",
    "fd_3m": "Fixed deposit 3–6 months", "fd_6m": "Fixed deposit 6–12 months",
    "fd_1y": "Fixed deposit 1–2 years", "fd_2y": "Fixed deposit 2–3 years",
    "fd_3y": "Fixed deposit 3 years+",
    "agriculture": "Agriculture", "term_large": "Term loan, large & medium industry",
    "term_small": "Term loan, small industry", "wc_large": "Working capital, large & medium",
    "wc_small": "Working capital, small industry", "export": "Export", "trade": "Trade finance",
    "housing": "Housing loan", "consumer": "Consumer credit", "card": "Credit card",
    "nbfi": "Credit to NBFIs", "others": "Others",
}

_NUM = re.compile(r"\d+(?:\.\d+)?")


class ParseError(ValueError):
    pass


@dataclass(frozen=True)
class PeerRateRow:
    month: date
    bank: str
    group: str
    book: str          # deposit | lending
    product: str
    low: Decimal
    high: Decimal


@dataclass
class PeerTable:
    month: date | None
    rows: list[PeerRateRow] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def rate_range(cell: str) -> tuple[Decimal, Decimal] | None:
    """'7.00-9.25' -> (7.00, 9.25); '3.00' -> (3.00, 3.00); '' or '-' -> None.

    A rate outside 0-40% is a misread cell, not a rate.
    """
    nums = []
    for m in _NUM.findall(cell or ""):
        try:
            nums.append(Decimal(m))
        except InvalidOperation:
            continue
    nums = [n for n in nums if Decimal(0) <= n <= Decimal(40)]
    if not nums:
        return None
    return min(nums), max(nums)


def month_of(text: str) -> date | None:
    """'August, 2026' -> 2026-08-01."""
    m = re.search(r"([A-Za-z]+)\s*,?\s*(\d{4})", text or "")
    if not m or m.group(1).lower() not in MONTHS:
        return None
    return date(int(m.group(2)), MONTHS[m.group(1).lower()], 1)


def month_end(d: date) -> date:
    nxt = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return nxt - timedelta(days=1)


def _cells(tr) -> list[str]:
    return [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]


def _rows(html: str):
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if table is None:
        raise ParseError("no table on the page")
    return [_cells(tr) for tr in table.find_all("tr")]


def _group_or_month(cells: list[str], state: dict) -> bool:
    """A one-cell row is the month or a group header. True when consumed."""
    filled = [c for c in cells if c]
    if len(filled) != 1:
        return False
    text = filled[0]
    m = month_of(text)
    if m:
        state["month"] = m
        return True
    g = GROUPS.get(re.sub(r"[^A-Za-z]", "", text).upper())
    if g:
        state["group"] = g
        return True
    return False


def deposit_table(html: str) -> PeerTable:
    rows = _rows(html)
    head = " ".join(" ".join(r) for r in rows[:2]).lower()
    out = PeerTable(None)
    if "savings" not in head or "fixed" not in head:
        out.warnings.append("the deposit table's headings have changed; read with care")
    state: dict = {"month": None, "group": None}
    for cells in rows[2:]:
        if _group_or_month(cells, state) or not cells or not cells[0]:
            continue
        if state["month"] is None or state["group"] is None:
            continue
        bank = cells[0].strip()
        for product, cell in zip(DEPOSIT_PRODUCTS, cells[1:]):
            r = rate_range(cell)
            if r:
                out.rows.append(PeerRateRow(state["month"], bank, state["group"], "deposit",
                                            product, *r))
    out.month = state["month"]
    if not out.rows:
        out.warnings.append("no bank rows found in the deposit table")
    return out


def lending_table(html: str) -> PeerTable:
    """Only each bank's first sub-category row (SC.1) is read: the further
    rows carry special schemes that would muddle a like-for-like comparison."""
    rows = _rows(html)
    out = PeerTable(None)
    head = " ".join(" ".join(r) for r in rows[:2]).lower()
    if "agriculture" not in head:
        out.warnings.append("the lending table's headings have changed; read with care")
    state: dict = {"month": None, "group": None}
    for cells in rows[2:]:
        if _group_or_month(cells, state) or len(cells) < 3:
            continue
        if state["month"] is None or state["group"] is None or not cells[0]:
            continue                       # a further sub-category of the bank above
        if not cells[1].upper().replace(" ", "").startswith("SC.1"):
            continue
        bank = cells[0].strip()
        for product, cell in zip(LENDING_PRODUCTS, cells[2:]):
            r = rate_range(cell)
            if r:
                out.rows.append(PeerRateRow(state["month"], bank, state["group"], "lending",
                                            product, *r))
    out.month = state["month"]
    if not out.rows:
        out.warnings.append("no bank rows found in the lending table")
    return out


@dataclass(frozen=True)
class IndustryMonth:
    month_end: date
    call_money: Decimal | None
    deposit_rate: Decimal | None
    advance_rate: Decimal | None
    spread: Decimal | None


def industry_rates(html: str) -> list[IndustryMonth]:
    """The interest-rate page: one row per month, under a year heading."""
    rows = _rows(html)
    year = None
    out = []
    for cells in rows:
        filled = [c for c in cells if c]
        if len(filled) == 1 and re.fullmatch(r"\d{4}", filled[0]):
            year = int(filled[0])
            continue
        if year is None or len(cells) < 7 or cells[0].lower() not in MONTHS:
            continue

        def num(s: str) -> Decimal | None:
            r = rate_range(s)
            return r[0] if r else None

        d = month_end(date(year, MONTHS[cells[0].lower()], 1))
        out.append(IndustryMonth(d, num(cells[3]), num(cells[4]), num(cells[5]), num(cells[6])))
    return sorted(out, key=lambda r: r.month_end)


@dataclass(frozen=True)
class PolicyRates:
    as_of: date | None
    repo: Decimal | None
    slf: Decimal | None
    sdf: Decimal | None
    bank_rate: Decimal | None
    last_mpc: date | None


def _after(lines: list[str], label: str) -> Decimal | None:
    for i, ln in enumerate(lines):
        if ln.lower().startswith(label.lower()):
            for nxt in lines[i + 1:i + 3]:
                m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*%", nxt.strip())
                if m:
                    return Decimal(m.group(1))
    return None


def policy_rates(text: str) -> PolicyRates:
    """The "POLICY RATES" box on Bangladesh Bank's home page, as text."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    start = next((i for i, ln in enumerate(lines) if ln.upper() == "POLICY RATES"), None)
    box = lines[start:start + 14] if start is not None else []
    as_of = None
    for ln in box:
        m = re.search(r"Last update:\s*(\d{1,2})\.(\d{1,2})\.(\d{4})", ln)
        if m:
            as_of = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            break
    mpc = None
    for m in re.finditer(r"MPC Resolution\s*:?\s*(\d{1,2})\s+([A-Za-z]+),?\s*(\d{4})", text):
        mon = MONTHS.get(m.group(2).lower())
        if mon:
            d = date(int(m.group(3)), mon, int(m.group(1)))
            mpc = max(mpc, d) if mpc else d
    return PolicyRates(as_of, _after(box, "Policy Rate"), _after(box, "SLF"),
                       _after(box, "SDF"), _after(box, "Bank Rate"), mpc)


@dataclass(frozen=True)
class CallMoneyDay:
    day: date
    overnight: Decimal | None          # weighted average, % a year
    volume: Decimal | None             # crore taka, overnight
    short_notice_7d: Decimal | None


def _long_day(text: str) -> date | None:
    m = re.fullmatch(r"(\d{1,2})\s+([A-Za-z]+),?\s*(\d{4})", text.strip())
    if not m or m.group(2).lower() not in MONTHS:
        return None
    return date(int(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1)))


def call_money_history(html: str) -> list[CallMoneyDay]:
    """Bangladesh Bank's call money table, which can list years of days: a
    date row, then one row per product and maturity with the day's highest,
    lowest and weighted average rate."""
    out = []
    day: date | None = None
    on = vol = sn7 = None

    def flush():
        if day is not None and (on is not None or sn7 is not None):
            out.append(CallMoneyDay(day, on, vol, sn7))

    for cells in _rows(html):
        if len(cells) == 1:
            d = _long_day(cells[0])
            if d:
                flush()
                day, on, vol, sn7 = d, None, None, None
            continue
        if day is None or len(cells) < 6:
            continue
        product, maturity = cells[0].lower(), cells[1].lower()
        days = re.match(r"(\d+)", maturity)
        avg = rate_range(cells[5])
        if product.startswith("overnight") or (product.startswith("call") and days
                                                and days.group(1) == "1"):
            on = avg[0] if avg else None
            try:
                vol = Decimal(cells[2].replace(",", ""))
            except InvalidOperation:
                vol = None
        elif product.startswith("short") and days and days.group(1) == "7":
            sn7 = avg[0] if avg else None
    flush()
    return sorted(out, key=lambda r: r.day)


# --- World Bank and IMF -------------------------------------------------------- #

def worldbank(payload) -> list[tuple[int, Decimal]]:
    """[(year, value)] from a World Bank v2 JSON response; empty years dropped."""
    if not isinstance(payload, list) or len(payload) < 2 or not payload[1]:
        return []
    out = []
    for o in payload[1]:
        try:
            if o.get("value") is not None:
                out.append((int(o["date"]), Decimal(str(o["value"]))))
        except (KeyError, ValueError, InvalidOperation, TypeError):
            continue
    return sorted(out)


def imf(payload, indicator: str, country: str = "BGD") -> list[tuple[int, Decimal]]:
    """[(year, value)] for one country from an IMF DataMapper response.

    The API answers with every country even when asked for one."""
    try:
        values = payload["values"][indicator][country]
    except (KeyError, TypeError):
        return []
    out = []
    for y, v in values.items():
        try:
            out.append((int(y), Decimal(str(v))))
        except (ValueError, InvalidOperation):
            continue
    return sorted(out)


# --- peer financials, pasted ---------------------------------------------------- #

PEER_METRICS = {
    "nim": "Net interest margin (%)",
    "cost_of_funds": "Cost of funds (%)",
    "cost_of_deposits": "Cost of deposits (%)",
    "yield_on_advances": "Yield on advances (%)",
    "deposit_growth": "Deposit growth, year on year (%)",
    "advance_growth": "Advance growth, year on year (%)",
    "casa": "CASA share of deposits (%)",
    "roa": "Return on assets (%)",
    "npl": "Non-performing loans (%)",
}
_ALIASES = {"cof": "cost_of_funds", "cod": "cost_of_deposits", "yoa": "yield_on_advances",
            "casa_ratio": "casa", "npl_ratio": "npl", "net_interest_margin": "nim"}


@dataclass(frozen=True)
class PeerFigure:
    bank: str
    period_end: date
    metric: str
    value: Decimal


def _period(s: str) -> date | None:
    s = s.strip()
    try:
        return date.fromisoformat(s)
    except ValueError:
        pass
    m = re.fullmatch(r"(?i)q([1-4])\s*[-/ ]?\s*(\d{4})|(\d{4})\s*[-/ ]?\s*q([1-4])", s)
    if m:
        q, y = (m.group(1), m.group(2)) if m.group(1) else (m.group(4), m.group(3))
        return month_end(date(int(y), int(q) * 3, 1))
    return None


def _metric(s: str) -> str | None:
    k = re.sub(r"[^a-z0-9]+", "_", s.strip().lower()).strip("_")
    k = _ALIASES.get(k, k)
    return k if k in PEER_METRICS else None


def peer_financials(text: str) -> tuple[list[PeerFigure], list[str]]:
    """Long (bank, period, metric, value) or wide (bank, period, nim, casa, …) CSV.

    Tabs and semicolons are accepted as separators, since a paste from a
    spreadsheet arrives tab-separated."""
    sample = text.strip()
    if not sample:
        return [], ["nothing was pasted"]
    delim = "\t" if "\t" in sample.splitlines()[0] else (";" if ";" in sample.splitlines()[0] else ",")
    rows = [r for r in csv.reader(io.StringIO(sample), delimiter=delim) if any(c.strip() for c in r)]
    head = [c.strip().lower() for c in rows[0]]
    out: list[PeerFigure] = []
    warnings: list[str] = []
    if len(head) < 3 or head[0] not in ("bank", "bank_code", "name") or \
            not any(h in ("period", "period_end", "quarter") for h in head[1:2]):
        return [], ["the first row must be headings: bank, period, then metric and value "
                    "(or one column per metric)"]
    long = head[2:4] == ["metric", "value"]
    metrics = [] if long else [_metric(h) for h in head[2:]]
    if not long:
        for h, m in zip(head[2:], metrics):
            if m is None:
                warnings.append(f"column {h!r} is not a known measure and was skipped")
    for i, r in enumerate(rows[1:], start=2):
        if len(r) < 3:
            continue
        bank, per = r[0].strip().upper(), _period(r[1])
        if not bank or per is None:
            warnings.append(f"row {i}: bank or period not readable")
            continue
        pairs = [(_metric(r[2]), r[3] if len(r) > 3 else "")] if long else list(zip(metrics, r[2:]))
        for m, v in pairs:
            if m is None or not str(v).strip():
                continue
            try:
                val = Decimal(str(v).replace("%", "").replace(",", "").strip())
            except InvalidOperation:
                warnings.append(f"row {i}: {v!r} is not a number")
                continue
            out.append(PeerFigure(bank, per, m, val))
    return out, warnings

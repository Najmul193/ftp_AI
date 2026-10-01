"""Detectors: small, deterministic rules that turn a fact sheet into findings.

Each detector answers one question a treasury or business head asks every
morning, with an explicit threshold and an explicit severity rule, so a
finding can always be explained by pointing at the rule and the numbers that
tripped it. None of them calls a model: the model's job (the brief) is to
narrate findings, never to decide them.

Money at stake is always computed here and always says what it measures
(`money_basis`), because "৳5.8 lakh" means nothing without "per month".

Text carries entity names as `{PRD:code}` / `{BR:code}` placeholders; see
`facts.fill`.

PURE: no I/O.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal

from app.ai.insights.facts import FactSheet, MarketPoint

#: Severity order, most urgent first. The bell shows the first two.
SEVERITIES = ("critical", "serious", "warning", "info")
BELL = frozenset({"critical", "serious"})

_Q2 = Decimal("0.01")
_DAYS_PER_MONTH = Decimal(30) / Decimal(365)


@dataclass(frozen=True)
class Evidence:
    label: str
    value: str                      # Decimal/date/text, as a string
    #: pct | bp | bdt | pct_change | date | text
    unit: str

    def to_dict(self) -> dict:
        return {"label": self.label, "value": self.value, "unit": self.unit}


@dataclass(frozen=True)
class Finding:
    kind: str
    subject: str
    severity: str
    title: str
    body: str
    #: "PUBLIC" for market findings anyone may see; "SCOPE" for the bank's own.
    audience: str = "SCOPE"
    money_at_stake: Decimal | None = None
    money_basis: str | None = None
    evidence: tuple[Evidence, ...] = ()
    action: dict | None = None
    sources: tuple[dict, ...] = field(default_factory=tuple)

    @property
    def rank(self) -> tuple[int, Decimal]:
        """Most urgent first, then most money."""
        return (SEVERITIES.index(self.severity), -abs(self.money_at_stake or Decimal(0)))


# --- formatting (for people; the provider gets `context.render`) ------------ #

def taka(x: Decimal | None) -> str:
    """BDT in the units a Bangladeshi banker reads: crore, lakh, taka."""
    if x is None:
        return "—"
    a = abs(Decimal(x))
    sign = "-" if x < 0 else ""
    if a >= 10_000_000:
        return f"{sign}৳{a / 10_000_000:.2f} crore"
    if a >= 100_000:
        return f"{sign}৳{a / 100_000:.2f} lakh"
    return f"{sign}৳{a:,.0f}"


def pct(x: Decimal | None, places: int = 2) -> str:
    """A rate to `places` decimals; with 3, a trailing zero is dropped
    (8.645% stays, 8.320% reads 8.32%)."""
    if x is None:
        return "—"
    s = f"{Decimal(x):.{places}f}"
    if places > 2 and s.endswith("0"):
        s = s[:-1]
    return f"{s}%"


def bp(delta_pct: Decimal) -> int:
    return int((Decimal(delta_pct) * 100).to_integral_value(rounding=ROUND_HALF_EVEN))


def signed_bp(delta_pct: Decimal) -> str:
    b = bp(delta_pct)
    return f"{'+' if b > 0 else ''}{b} bp"


def tenor(days: int) -> str:
    if days <= 1:
        return "overnight"
    if days < 30:
        return f"{days}-day"
    if days < 365:
        return {91: "3-month", 182: "6-month", 364: "1-year"}.get(days, f"{round(days / 30)}-month")
    return f"{round(days / 365)}-year"


def _step_down(sev: str) -> str:
    i = SEVERITIES.index(sev)
    return SEVERITIES[min(i + 1, len(SEVERITIES) - 1)]


def _ev(label: str, value, unit: str) -> Evidence:
    return Evidence(label, "" if value is None else str(value), unit)


_BB = {"label": "Bangladesh Bank", "url": "https://www.bb.org.bd"}
_BOOK = {"label": "FTP platform analytics", "metric": "book"}


# --- 1. benchmarks against the market (head office) ------------------------- #

DRIFT_MIN_BP = 50
DRIFT_MIN_IMPACT = Decimal(300_000)          # per month
DRIFT_SERIOUS_BP = 100
DRIFT_SERIOUS_IMPACT = Decimal(500_000)
#: A curve point older than this weakens any conclusion drawn from it.
MARKET_STALE_DAYS = 21


def benchmark_drift(fs: FactSheet) -> list[Finding]:
    out = []
    for g in fs.benchmarks:
        if g.behavioural or g.gap_bp is None or g.benchmark is None or g.market is None:
            continue
        impact = g.monthly_impact
        big_gap = abs(g.gap_bp) >= DRIFT_MIN_BP
        big_money = impact is not None and abs(impact) >= DRIFT_MIN_IMPACT
        if not (big_gap or big_money):
            continue
        serious = abs(g.gap_bp) >= DRIFT_SERIOUS_BP or (
            impact is not None and abs(impact) >= DRIFT_SERIOUS_IMPACT)
        sev = "serious" if serious else "warning"
        stale = g.market_as_of is not None and (fs.today - g.market_as_of).days > MARKET_STALE_DAYS
        if stale:
            sev = _step_down(sev)

        below = g.gap_bp < 0
        where = "below" if below else "above"
        term = tenor(g.tenor_days)
        per_month = f"about {taka(abs(impact))} a month" if impact else "an amount not yet measured"
        if g.side == "LIABILITY":
            effect = (f"Branches gathering it are credited {per_month} less than a market-based "
                      f"benchmark would give them, so deposit gathering looks less profitable "
                      f"than it is." if below else
                      f"Branches gathering it are credited {per_month} more than the market "
                      f"supports, so deposit gathering looks more profitable than it is.")
        else:
            effect = (f"Lending is charged {per_month} less for its funds than the market cost, "
                      f"so these loans look more profitable than they are." if below else
                      f"Lending is charged {per_month} more for its funds than the market cost, "
                      f"so these loans look less profitable than they are.")
        market_note = f" The market rate is read from the curve at {g.market_basis}" + (
            f", last recorded {g.market_as_of:%d %b}." if g.market_as_of else ".")
        if stale:
            market_note += " That point is old; confirm it before acting."
        # A proposal, not a measurement: rounded the way a rate is quoted.
        suggested = Decimal(g.market).quantize(_Q2, rounding=ROUND_HALF_UP)
        note = (f"Market-linked review: benchmark {pct(g.benchmark)} is {abs(g.gap_bp)} bp {where} "
                f"the {term} market rate {pct(g.market, 3)} ({g.market_basis}"
                + (f", {g.market_as_of:%d %b %Y}" if g.market_as_of else "") + "). "
                f"Proposed {pct(suggested)}."
                + (f" Effect on the current balance of {taka(g.balance)}: {taka(abs(impact))} a month."
                   if impact and g.balance else "")
                + " Prepared by FTP Intelligence.")[:500]
        out.append(Finding(
            kind="benchmark_drift", subject=g.product_code, severity=sev,
            title=f"{{PRD:{g.product_code}}}: benchmark {abs(g.gap_bp)} bp {where} the {term} market",
            body=(f"The FTP benchmark is {pct(g.benchmark)}; money of the same term costs "
                  f"{pct(g.market, 3)} in the market. {effect}{market_note}"),
            money_at_stake=abs(impact) if impact is not None else None,
            money_basis="per month" if impact is not None else None,
            evidence=(
                _ev("FTP benchmark", g.benchmark, "pct"),
                _ev(f"Market, {term}", g.market, "pct"),
                _ev("Gap", g.gap_bp, "bp"),
                _ev("Balance", g.balance, "bdt"),
                _ev("Term basis", g.tenor_basis, "text"),
            ),
            action={"type": "prepare_rate_change", "product_code": g.product_code,
                    "current": str(g.benchmark), "suggested": str(suggested),
                    "tenor": term, "note": note},
            sources=(_BB, {"label": "Rate configuration", "metric": "rates"}),
        ))
    return out


# --- 2. the market ------------------------------------------------------------ #

#: (code, warning threshold, serious threshold, unit of the threshold)
MOVES: tuple[tuple[str, Decimal, Decimal, str], ...] = (
    ("BB_CALL_ON", Decimal("0.25"), Decimal("0.50"), "pp"),
    ("BB_DOMMR_1M", Decimal("0.25"), Decimal("0.50"), "pp"),
    ("BB_TBILL_91", Decimal("0.25"), Decimal("0.50"), "pp"),
    ("BB_TBILL_182", Decimal("0.25"), Decimal("0.50"), "pp"),
    ("BB_TBILL_364", Decimal("0.25"), Decimal("0.50"), "pp"),
    ("BB_TBOND_2Y", Decimal("0.25"), Decimal("0.50"), "pp"),
    ("BB_TBOND_5Y", Decimal("0.25"), Decimal("0.50"), "pp"),
    ("BB_TBOND_10Y", Decimal("0.25"), Decimal("0.50"), "pp"),
    ("FX_USDBDT", Decimal("1.0"), Decimal("2.5"), "pct"),
    ("US_FEDFUNDS", Decimal("0.20"), Decimal("0.45"), "pp"),
    ("US_UST_10Y", Decimal("0.30"), Decimal("0.60"), "pp"),
    ("BRENT", Decimal("10"), Decimal("20"), "pct"),
)
#: Global moves inform; they do not by themselves demand a decision here.
_GLOBAL = {"US_FEDFUNDS", "US_UST_10Y", "BRENT"}


def _reference(p: MarketPoint) -> tuple[Decimal, date] | None:
    """What to compare today's value with: a week ago, else the last value
    within ten days (an auction series moves once a week)."""
    if p.week_ago is not None and p.week_ago_as_of and p.as_of \
            and (p.as_of - p.week_ago_as_of).days <= 14:
        return p.week_ago, p.week_ago_as_of
    if p.prev is not None and p.prev_as_of and p.as_of and (p.as_of - p.prev_as_of).days <= 10:
        return p.prev, p.prev_as_of
    return None


def market_moves(fs: FactSheet) -> list[Finding]:
    out = []
    for code, warn, serious, unit in MOVES:
        p = fs.market.get(code)
        if p is None or p.value is None or p.as_of is None or p.stale:
            continue
        ref = _reference(p)
        if ref is None:
            continue
        before, since = ref
        delta = Decimal(p.value) - Decimal(before)
        size = abs(delta) if unit == "pp" else (abs(delta) / abs(Decimal(before)) * 100
                                                  if before else Decimal(0))
        if size < warn:
            continue
        sev = "serious" if size >= serious else "warning"
        if code in _GLOBAL:
            sev = "warning" if sev == "serious" else "info"
        up = delta > 0
        if unit == "pp":
            move = f"{'up' if up else 'down'} {abs(bp(delta))} bp"
            now = pct(p.value, 2)
            was = pct(before, 2)
        else:
            move = f"{'up' if up else 'down'} {size:.1f}%"
            now, was = f"{Decimal(p.value):.2f}", f"{Decimal(before):.2f}"
        meaning = {
            "BB_CALL_ON": "Overnight money is the marginal cost of funding the book; a sustained "
                          "move feeds every short-term benchmark.",
            "BB_DOMMR_1M": "The one-month reference rate prices short-term interbank funding.",
            "FX_USDBDT": "A weaker taka raises import-linked credit demand and dollar funding costs.",
            "US_FEDFUNDS": "Global dollar funding costs follow the Fed.",
            "US_UST_10Y": "Long-term dollar yields set the tone for global borrowing costs.",
            "BRENT": "Oil feeds Bangladesh's import bill, the taka and inflation.",
        }.get(code, "Government paper at this term is the market price of money for matching "
                    "loans and deposits.")
        out.append(Finding(
            kind="market_move", subject=code, severity=sev, audience="PUBLIC",
            title=f"{p.short} {move} since {since:%d %b}",
            body=f"{p.short} is {now}, from {was} on {since:%d %b}. {meaning}",
            evidence=(_ev(p.short, p.value if unit == "pp" else now, "pct" if unit == "pp" else "text"),
                      _ev(f"On {since:%d %b}", before if unit == "pp" else was,
                          "pct" if unit == "pp" else "text"),
                      _ev("Change", bp(delta) if unit == "pp" else f"{size:.1f}",
                          "bp" if unit == "pp" else "pct_change")),
            sources=(_BB,) if code.startswith("BB_") else (
                {"label": "FRED, Federal Reserve Bank of St. Louis", "url": "https://fred.stlouisfed.org"},)
            if code.startswith(("US_", "BRENT")) else ({"label": "ExchangeRate-API",
                                                       "url": "https://www.exchangerate-api.com"},),
        ))
    return out


def policy_rate(fs: FactSheet) -> list[Finding]:
    p = fs.market.get("BB_POLICY")
    if p is None or p.value is None or p.prev is None or p.as_of is None:
        return []
    delta = Decimal(p.value) - Decimal(p.prev)
    if delta == 0 or (fs.today - p.as_of).days > 30:
        return []
    verb = "raised" if delta > 0 else "cut"
    return [Finding(
        kind="policy_rate", subject="BB_POLICY",
        severity="critical" if abs(delta) >= Decimal("0.5") else "serious", audience="PUBLIC",
        title=f"Bangladesh Bank {verb} the policy rate by {abs(bp(delta))} bp to {pct(p.value)}",
        body=(f"Effective {p.as_of:%d %b %Y}, from {pct(p.prev)}. The whole taka curve reprices "
              f"from here: review every FTP benchmark, starting with the short-term ones."),
        evidence=(_ev("Policy rate", p.value, "pct"), _ev("Before", p.prev, "pct"),
                  _ev("Change", bp(delta), "bp")),
        sources=(_BB,),
    )]


POLICY_NEWS_MIN_RELEVANCE = 5


def policy_news(fs: FactSheet) -> list[Finding]:
    """The most relevant monetary-policy story of the last three days."""
    cands = [n for n in fs.news if "policy_rate" in n.tags
             and n.relevance >= POLICY_NEWS_MIN_RELEVANCE]
    if not cands:
        return []
    n = max(cands, key=lambda x: (x.relevance, x.published_at or ""))
    key = re.sub(r"[^a-z0-9]+", "", n.title.lower())[:60]
    direction = {1: " The story points to rates rising.", -1: " The story points to rates easing."}
    return [Finding(
        kind="policy_news", subject=key, severity="info", audience="PUBLIC",
        title=f"In the news: {n.title}",
        body=f"Reported by {n.source}.{direction.get(n.rate_signal, '')}",
        sources=({"label": n.source, "url": n.url},),
    )]


def stale_curve(fs: FactSheet) -> list[Finding]:
    old = [p for p in fs.market.values()
           if p.category in ("bd_money", "bd_govt") and p.tenor_days and p.as_of and p.stale]
    missing = [p for p in fs.market.values()
               if p.code in ("BB_CALL_ON", "BB_TBILL_91", "BB_TBILL_364") and p.value is None]
    if not old and not missing:
        return []
    parts = [f"{p.short} (last {p.as_of:%d %b})" for p in old if p.as_of] + \
            [f"{p.short} (never recorded)" for p in missing]
    return [Finding(
        kind="stale_curve", subject="", severity="warning" if missing else "info",
        audience="PUBLIC",
        title="Parts of the taka curve are out of date",
        body=(f"{', '.join(parts)}. Benchmark comparisons at those terms rest on old prices "
              f"until Bangladesh Bank's pages are read again or treasury enters them."),
        sources=(_BB,),
    )]


# --- 3. the bank's own book ------------------------------------------------- #

DATA_LAG_WARN = 2
DATA_LAG_SERIOUS = 5


def data_freshness(fs: FactSheet) -> list[Finding]:
    lag = fs.data_lag_days
    if lag is None or lag <= DATA_LAG_WARN:
        return []
    return [Finding(
        kind="data_stale", subject="", severity="serious" if lag > DATA_LAG_SERIOUS else "warning",
        title=f"Bank data is {lag} days old",
        body=(f"The latest business date loaded is {fs.business_date:%d %b %Y}. Everything about "
              f"the book below describes that day, not today."),
        evidence=(_ev("Latest business date", fs.business_date, "date"),),
        sources=({"label": "Upload", "metric": "upload"},),
    )]


NIM_WARN, NIM_SERIOUS = Decimal("-0.10"), Decimal("-0.25")
COD_WARN, COD_SERIOUS = Decimal("0.10"), Decimal("0.25")


def margin(fs: FactSheet) -> list[Finding]:
    out = []
    window = _window_words(fs)
    nim = fs.ratios.get("nim")
    adv = fs.book.get("advances")
    if nim and nim.change is not None and nim.change <= NIM_WARN:
        money = (adv.current * nim.change / 100 * _DAYS_PER_MONTH).quantize(_Q2) \
            if adv and adv.current else None
        out.append(Finding(
            kind="nim_compression", subject="",
            severity="serious" if nim.change <= NIM_SERIOUS else "warning",
            title=f"Net interest margin down {abs(bp(nim.change))} bp {window}",
            body=(f"NIM is {pct(nim.current)}, from {pct(nim.prior)}. On today's advances that is "
                  f"about {taka(abs(money)) if money else '—'} a month of margin."),
            money_at_stake=abs(money) if money else None, money_basis="per month",
            evidence=(_ev("NIM now", nim.current, "pct"), _ev("NIM before", nim.prior, "pct"),
                      _ev("Change", bp(nim.change), "bp")),
            sources=(_BOOK,),
        ))
    cod = fs.ratios.get("cost_of_deposits")
    dep = fs.book.get("deposits")
    if cod and cod.change is not None and cod.change >= COD_WARN:
        money = (dep.current * cod.change / 100 * _DAYS_PER_MONTH).quantize(_Q2) \
            if dep and dep.current else None
        out.append(Finding(
            kind="cof_rise", subject="",
            severity="serious" if cod.change >= COD_SERIOUS else "warning",
            title=f"Cost of deposits up {bp(cod.change)} bp {window}",
            body=(f"Deposits now cost {pct(cod.current)} a year, from {pct(cod.prior)}: about "
                  f"{taka(money) if money else '—'} a month more on today's deposits."),
            money_at_stake=money, money_basis="per month",
            evidence=(_ev("Cost of deposits now", cod.current, "pct"),
                      _ev("Before", cod.prior, "pct"), _ev("Change", bp(cod.change), "bp")),
            sources=(_BOOK,),
        ))
    return out


RUNOFF_WARN, RUNOFF_SERIOUS = Decimal(-2), Decimal(-5)
CASA_WARN, CASA_SERIOUS = Decimal("-1.0"), Decimal("-2.5")


def deposits(fs: FactSheet) -> list[Finding]:
    out = []
    dep = fs.book.get("deposits")
    when = _book_words(fs)
    if dep and dep.change_pct is not None and dep.change is not None \
            and dep.change_pct <= RUNOFF_WARN:
        out.append(Finding(
            kind="deposit_runoff", subject="",
            severity="serious" if dep.change_pct <= RUNOFF_SERIOUS else "warning",
            title=f"Deposits down {abs(dep.change_pct):.1f}% {when}",
            body=(f"Deposits are {taka(dep.current)}, from {taka(dep.prior)}: {taka(abs(dep.change))} "
                  f"less to fund the book, which treasury has to replace at market rates."),
            money_at_stake=abs(dep.change), money_basis="of deposits",
            evidence=(_ev("Deposits now", dep.current, "bdt"), _ev("Before", dep.prior, "bdt"),
                      _ev("Change", f"{dep.change_pct:.2f}", "pct_change")),
            sources=(_BOOK,),
        ))
    casa = fs.book.get("casa_ratio")
    if casa and casa.change is not None and casa.change <= CASA_WARN:
        out.append(Finding(
            kind="casa_shift", subject="",
            severity="serious" if casa.change <= CASA_SERIOUS else "warning",
            title=f"CASA share down {abs(casa.change):.1f} points {when}",
            body=(f"Current and savings accounts are {pct(casa.current, 1)} of deposits, from "
                  f"{pct(casa.prior, 1)}. The cheapest funding is shrinking as a share of the mix."),
            evidence=(_ev("CASA ratio now", casa.current, "pct"), _ev("Before", casa.prior, "pct")),
            sources=(_BOOK,),
        ))
    return out


PROFIT_DOWN_WARN, PROFIT_DOWN_SERIOUS = Decimal(-5), Decimal(-15)
PROFIT_UP_NOTE = Decimal(10)


def profit(fs: FactSheet) -> list[Finding]:
    net = fs.profit.get("net")
    if net is None or net.change_pct is None or not fs.prior_has_data:
        return []
    ch = net.change_pct
    if PROFIT_DOWN_WARN < ch < PROFIT_UP_NOTE:
        return []
    window = _window_words(fs)
    why = _drivers(fs)
    if ch <= PROFIT_DOWN_WARN:
        sev = "serious" if ch <= PROFIT_DOWN_SERIOUS else "warning"
        title = f"FTP profit down {abs(ch):.1f}% {window}"
    else:
        sev = "info"
        title = f"FTP profit up {ch:.1f}% {window}"
    return [Finding(
        kind="profit_swing", subject="", severity=sev, title=title,
        body=f"Net FTP profit was {taka(net.current)}, against {taka(net.prior)}. {why}",
        money_at_stake=abs(net.change) if net.change is not None else None,
        money_basis="over the period",
        evidence=(_ev("Net FTP profit", net.current, "bdt"), _ev("Prior period", net.prior, "bdt"),
                  _ev("Change", f"{ch:.2f}", "pct_change")),
        sources=(_BOOK,),
    )]


BRANCH_DROP_MIN = Decimal(50_000)
BRANCH_DROP_SHARE = Decimal("0.10")


def branch_movers(fs: FactSheet) -> list[Finding]:
    out = []
    losers = sorted((s for s in fs.branch_movers if s.change < 0), key=lambda s: s.change)
    for s in losers[:3]:
        if abs(s.change) < BRANCH_DROP_MIN or (
                s.prior_profit > 0 and abs(s.change) < s.prior_profit * BRANCH_DROP_SHARE):
            continue
        driver = "lower balances" if abs(s.volume) >= abs(s.rate) else "thinner spreads"
        out.append(Finding(
            kind="branch_drop", subject=s.key, severity="info",
            title=f"{{BR:{s.key}}}: FTP profit down {taka(abs(s.change))} {_window_words(fs)}",
            body=(f"From {taka(s.prior_profit)} to {taka(s.current_profit)}, mostly from {driver} "
                  f"(volume {taka(s.volume)}, rate {taka(s.rate)})."),
            money_at_stake=abs(s.change), money_basis="over the period",
            evidence=(_ev("Profit now", s.current_profit, "bdt"),
                      _ev("Prior period", s.prior_profit, "bdt"),
                      _ev("Volume effect", s.volume, "bdt"), _ev("Rate effect", s.rate, "bdt")),
            sources=(_BOOK,),
        ))
    return out


# --- 9. pricing against the market's posted rates (head office) -------------- #

#: Balances smaller than this are not worth a finding.
PEER_MIN_BALANCE = Decimal(50_000_000)               # ৳5 crore
PEER_SERIOUS_BALANCE = Decimal(500_000_000)          # ৳50 crore
#: A deposit paying this far under the private banks' median is exposed.
PEER_DEPOSIT_GAP = Decimal("-0.75")
#: A loan this far from the market's median is mispriced one way or the other.
PEER_LOAN_GAP = Decimal("1.00")
_PEER = {"label": "Bangladesh Bank, bank-wise interest rates",
         "url": "https://www.bb.org.bd/en/index.php/financialactivity/interestdeposit"}


def _month_of(d: date | None) -> str:
    return f"{d:%B %Y}" if d else "the latest month"


def peer_pricing(fs: FactSheet) -> list[Finding]:
    out = []
    for g in fs.peer_gaps:
        if g.balance < PEER_MIN_BALANCE:
            continue
        ref = g.pcb_median if g.pcb_median is not None else g.market_median
        gap = (g.our_rate - ref).quantize(_Q2)
        ev = (_ev("Our customer rate", g.our_rate, "pct"),
              _ev("Private banks' median", g.pcb_median, "pct"),
              _ev("All banks' median", g.market_median, "pct"),
              _ev("Market's middle half", f"{pct(g.p25)}–{pct(g.p75)}", "text"),
              _ev("Balance", g.balance, "bdt"))
        if g.side == "LIABILITY":
            if gap > PEER_DEPOSIT_GAP and (g.p25 is None or g.our_rate >= g.p25):
                continue
            cost = (g.balance * (ref - g.our_rate) / 100 * _DAYS_PER_MONTH).quantize(Decimal(1))
            below_most = g.p25 is not None and g.our_rate < g.p25
            sev = "serious" if (below_most and g.balance >= PEER_SERIOUS_BALANCE) else "warning"
            out.append(Finding(
                kind="peer_deposit_rate", subject=g.product_code, severity=sev,
                title=(f"{{PRD:{g.product_code}}} pays {abs(bp(gap))} bp under the private banks "
                       f"({taka(g.balance)} exposed)"),
                body=(f"Customers get {pct(g.our_rate)} on {taka(g.balance)}; private banks post a "
                      f"median {pct(ref)} for {g.peer_label.lower()} ({_month_of(g.month)})"
                      + (", and we are below three-quarters of all banks" if below_most else "")
                      + f". Depositors who shop around can move. Matching the median would cost "
                        f"about {taka(cost)} a month; losing the balance would cost its funding."),
                money_at_stake=cost, money_basis="a month to match private banks",
                evidence=ev, sources=(_PEER, _BOOK),
                action={"type": "scenario", "label": "Simulate matching the private banks",
                        "scenario": {"product_rate_bp": {g.product_code: abs(bp(gap))},
                                     "horizon_months": 6}}))
        else:
            if abs(gap) < PEER_LOAN_GAP:
                continue
            if gap > 0:
                above_most = g.p75 is not None and g.our_rate > g.p75
                premium = (g.balance * gap / 100 * _DAYS_PER_MONTH).quantize(Decimal(1))
                out.append(Finding(
                    kind="peer_loan_rate_high", subject=g.product_code,
                    severity="warning" if above_most else "info",
                    title=(f"{{PRD:{g.product_code}}} is priced {bp(gap)} bp above the private "
                           f"banks ({taka(g.balance)} exposed)"),
                    body=(f"Borrowers pay {pct(g.our_rate)} on {taka(g.balance)}; private banks "
                          f"post a median {pct(ref)} for {g.peer_label.lower()} "
                          f"({_month_of(g.month)}). Good borrowers can refinance elsewhere."),
                    money_at_stake=premium, money_basis="a month of premium at risk",
                    evidence=ev, sources=(_PEER, _BOOK)))
            else:
                lost = (g.balance * (ref - g.our_rate) / 100 * _DAYS_PER_MONTH).quantize(Decimal(1))
                out.append(Finding(
                    kind="peer_loan_rate_low", subject=g.product_code, severity="warning",
                    title=f"{{PRD:{g.product_code}}} is priced {abs(bp(gap))} bp under the market",
                    body=(f"Borrowers pay {pct(g.our_rate)} on {taka(g.balance)}; private banks "
                          f"post a median {pct(ref)} for {g.peer_label.lower()} "
                          f"({_month_of(g.month)}). Pricing new and renewing loans nearer the "
                          f"market would earn about {taka(lost)} a month more."),
                    money_at_stake=lost, money_basis="per month",
                    evidence=ev, sources=(_PEER, _BOOK),
                    action={"type": "scenario", "label": "Simulate repricing to the market",
                            "scenario": {"product_rate_bp": {g.product_code: abs(bp(gap))},
                                         "horizon_months": 12}}))
    return out


# --- 10. where the month is heading ----------------------------------------------- #

LANDING_DEPOSIT_DROP = Decimal("-2")          # % by month-end


def landing(fs: FactSheet) -> list[Finding]:
    out = []
    np_ = fs.landings.get("net_ftp_profit")
    if np_ and np_.previous_month:
        prev = np_.previous_month
        if np_.p90 < prev:
            short = prev - np_.p50
            out.append(Finding(
                kind="landing_profit", subject="", severity="warning" if np_.p90 < prev * Decimal(
                    "0.95") else "info",
                title=f"FTP profit on course to finish {_month_of(np_.month_end)} below last month",
                body=(f"So far {taka(np_.so_far)}. The forecast for the month is {taka(np_.p50)} "
                      f"(likely range {taka(np_.p10)}–{taka(np_.p90)}), against {taka(prev)} last "
                      f"month. Confidence: {np_.confidence}."),
                money_at_stake=short, money_basis="short of last month",
                evidence=(_ev("Forecast", np_.p50, "bdt"), _ev("Low", np_.p10, "bdt"),
                          _ev("High", np_.p90, "bdt"), _ev("Last month", prev, "bdt")),
                sources=(_BOOK,)))
        elif np_.p10 > prev:
            out.append(Finding(
                kind="landing_profit", subject="", severity="info",
                title=f"FTP profit on course to beat last month",
                body=(f"The forecast for {_month_of(np_.month_end)} is {taka(np_.p50)} "
                      f"(likely range {taka(np_.p10)}–{taka(np_.p90)}), against {taka(prev)} "
                      f"last month."),
                money_at_stake=np_.p50 - prev, money_basis="above last month",
                evidence=(_ev("Forecast", np_.p50, "bdt"), _ev("Last month", prev, "bdt")),
                sources=(_BOOK,)))
    dep = fs.landings.get("deposits")
    if dep and dep.last:
        move = (dep.p50 - dep.last) / dep.last * 100
        if move <= LANDING_DEPOSIT_DROP and dep.month_end > (fs.business_date or dep.month_end):
            out.append(Finding(
                kind="landing_deposits", subject="", severity="warning",
                title=f"Deposits heading {abs(move):.1f}% lower by {dep.month_end:%d %b}",
                body=(f"Today {taka(dep.last)}; the month-end forecast is {taka(dep.p50)} "
                      f"(likely range {taka(dep.p10)}–{taka(dep.p90)}). Confidence: "
                      f"{dep.confidence}."),
                money_at_stake=dep.last - dep.p50, money_basis="by month-end",
                evidence=(_ev("Today", dep.last, "bdt"), _ev("Month-end forecast", dep.p50, "bdt")),
                sources=(_BOOK,)))
    return out


# --- 11. the next policy meeting (public) ----------------------------------------- #

def policy_outlook(fs: FactSheet) -> list[Finding]:
    p = fs.policy
    if p is None or p.next_meeting is None:
        return []
    side = max(("hike", "cut"), key=lambda k: p.odds.get(k, 0))
    if p.leaning == "hold" and p.odds.get(side, 0) < 30:
        return []
    when = f"{p.next_meeting:%d %b}"
    lean = (f"lean towards a {p.leaning}" if p.leaning != "hold"
            else f"lean to a hold, with a {side} the main risk")
    return [Finding(
        kind="policy_outlook", subject=p.next_meeting.isoformat(),
        severity="warning" if p.leaning != "hold" else "info",
        title=f"Next MPC (about {when}): signals {lean}",
        body=(f"Repo is {pct(p.repo)}. Signals: hike {p.odds.get('hike', 0)}%, hold "
              f"{p.odds.get('hold', 0)}%, cut {p.odds.get('cut', 0)}% -- a summary of the "
              f"signals, not a market price. " + " ".join(p.top)),
        audience="PUBLIC",
        evidence=tuple(_ev(k.title(), f"{v}%", "text") for k, v in p.odds.items()),
        sources=(_BB, {"label": "IMF World Economic Outlook",
                       "url": "https://www.imf.org/external/datamapper"})),
    ]


# --- 12. other banks changing their posted rates (public) ------------------------ #

COMPETITOR_MOVE_BP = 50


def competitor_moves(fs: FactSheet) -> list[Finding]:
    out = []
    for m in fs.rate_moves[:6]:
        if abs(m["change_bp"]) < COMPETITOR_MOVE_BP:
            continue
        up = m["change_bp"] > 0
        deposit = m["book"] == "deposit"
        what = ("pays more on" if up else "pays less on") if deposit else \
            ("charges more for" if up else "charges less for")
        out.append(Finding(
            kind="competitor_move", subject=f"{m['code']}:{m['product']}"[:60],
            severity="warning" if (deposit and up) or (not deposit and not up) else "info",
            title=f"{m['name']} now {what} {m['label'].lower()} ({'+' if up else ''}{m['change_bp']} bp)",
            body=(f"Its posted rate went from {pct(m['from'])} to {pct(m['to'])} "
                  f"({_month_of(m['month'])}). "
                  + ("Depositors comparing rates may move." if deposit and up else
                     "Borrowers comparing prices may move." if not deposit and not up else
                     "Room for us to hold or adjust our own price.")),
            audience="PUBLIC",
            evidence=(_ev("Before", m["from"], "pct"), _ev("Now", m["to"], "pct"),
                      _ev("Change", m["change_bp"], "bp")),
            sources=(_PEER,)))
    return out


# --- running them ------------------------------------------------------------ #

def _window_words(fs: FactSheet) -> str:
    if fs.window and fs.prior_window:
        days = (fs.window[1] - fs.window[0]).days + 1
        return "week on week" if days == 7 else f"on the previous {days} days"
    return "on the previous period"


def _book_words(fs: FactSheet) -> str:
    if fs.prior_window:
        return f"since {fs.prior_window[1]:%d %b}"
    return "on the previous period"


def _drivers(fs: FactSheet) -> str:
    b = fs.bridge
    if not b:
        return ""
    vol, rate = Decimal(b["volume"]), Decimal(b["rate"])
    main = "volume (balances)" if abs(vol) >= abs(rate) else "rate (spreads)"
    return (f"Mostly {main}: volume {taka(vol)}, rate {taka(rate)}.")


BOOK_DETECTORS = (data_freshness, margin, deposits, profit, branch_movers, landing)
HO_DETECTORS = (benchmark_drift, peer_pricing)
PUBLIC_DETECTORS = (policy_rate, market_moves, policy_news, stale_curve, policy_outlook,
                    competitor_moves)


def run(fs: FactSheet, *, include_public: bool, head_office: bool) -> list[Finding]:
    """Every finding for one scope, most urgent first."""
    found: list[Finding] = []
    if fs.has_book:
        for d in BOOK_DETECTORS:
            found += d(fs)
    if head_office:
        for d in HO_DETECTORS:
            found += d(fs)
    if include_public:
        for d in PUBLIC_DETECTORS:
            found += d(fs)
    return sorted(found, key=lambda f: f.rank)

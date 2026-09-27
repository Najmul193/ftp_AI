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


BOOK_DETECTORS = (data_freshness, margin, deposits, profit, branch_movers)
HO_DETECTORS = (benchmark_drift,)
PUBLIC_DETECTORS = (policy_rate, market_moves, policy_news, stale_curve)


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

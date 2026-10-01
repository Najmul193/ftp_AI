"""The fact sheet: everything the detectors and the brief are allowed to know.

Built by `app.ai.context.fact_sheet` from the platform's own analytics and the
market tables; consumed by pure code (detectors, the brief, the renderer that
prepares text for a provider). Every figure here was computed by the platform,
never by a model -- "code computes, the model narrates".

Entity names are not stored in the text of anything derived from this: they
appear as `{BR:0101}`-style placeholders and are filled at the last moment,
with real names for a reader or with vault tokens for a provider (`fill`).

PURE: no I/O.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Callable

_PLACEHOLDER = re.compile(r"\{(BR|PRD|DIST|DIV):([^{}]+)\}")


@dataclass(frozen=True)
class Delta:
    current: Decimal | None
    prior: Decimal | None

    @property
    def change(self) -> Decimal | None:
        if self.current is None or self.prior is None:
            return None
        return self.current - self.prior

    @property
    def change_pct(self) -> Decimal | None:
        c = self.change
        if c is None or not self.prior:
            return None
        return c / abs(self.prior) * 100


@dataclass(frozen=True)
class MarketPoint:
    code: str
    short: str
    unit: str
    category: str
    value: Decimal | None
    as_of: date | None
    prev: Decimal | None = None
    prev_as_of: date | None = None
    #: The latest value at least seven days before `as_of`.
    week_ago: Decimal | None = None
    week_ago_as_of: date | None = None
    stale: bool = False
    tenor_days: int | None = None
    source: str = ""


@dataclass(frozen=True)
class BenchmarkGap:
    product_code: str
    name: str
    side: str                       # ASSET | LIABILITY
    tenor_days: int
    tenor_basis: str
    benchmark: Decimal | None
    market: Decimal | None
    market_basis: str | None
    gap_bp: int | None
    balance: Decimal | None
    monthly_impact: Decimal | None
    behavioural: bool
    #: The oldest date among the curve points the market rate was read from.
    market_as_of: date | None = None


@dataclass(frozen=True)
class Segment:
    """One row of a variance bridge: a product or a branch."""
    kind: str                       # product | branch
    key: str
    change: Decimal
    volume: Decimal
    rate: Decimal
    prior_profit: Decimal
    current_profit: Decimal


@dataclass(frozen=True)
class NewsLine:
    source: str
    title: str
    url: str
    published_at: str | None
    rate_signal: int
    relevance: int
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class PeerGap:
    """One of this bank's products against the market's posted rates for the
    like product (Bangladesh Bank's bank-by-bank table)."""
    product_code: str
    side: str                       # ASSET | LIABILITY
    peer_label: str
    our_rate: Decimal               # what customers actually get, balance-weighted
    market_median: Decimal
    pcb_median: Decimal | None      # private commercial banks: the real competitors
    p25: Decimal | None
    p75: Decimal | None
    balance: Decimal
    month: date | None


@dataclass(frozen=True)
class Landing:
    """Where a measure is heading by month-end: a forecast with its band."""
    metric: str
    label: str
    unit: str                       # bdt | pct
    kind: str                       # stock | flow | rate
    month_end: date
    last: Decimal
    p10: Decimal
    p50: Decimal
    p90: Decimal
    #: For a flow: the month so far and the whole of last month.
    so_far: Decimal | None = None
    previous_month: Decimal | None = None
    confidence: str = "low"


@dataclass(frozen=True)
class PolicyView:
    leaning: str                    # hike | hold | cut
    odds: dict
    next_meeting: date | None
    repo: Decimal | None
    #: The two signals pushing hardest, in words.
    top: tuple[str, ...] = ()


@dataclass
class FactSheet:
    scope_key: str                  # HO | DIV:<id> | PUBLIC
    scope_label: str
    today: date
    #: Market data, benchmark gaps and news are PUBLIC-safe except the gaps'
    #: balances, which are bank figures. Everything else is bank data.
    business_date: date | None = None
    window: tuple[date, date] | None = None
    prior_window: tuple[date, date] | None = None
    prior_has_data: bool = False
    #: Flows over the window against the prior window: net, lending, deposit.
    profit: dict[str, Delta] = field(default_factory=dict)
    #: Stocks on the latest day against the last day of the prior window.
    book: dict[str, Delta] = field(default_factory=dict)
    #: Annualised ratios for the window against the prior window, in percent.
    ratios: dict[str, Delta] = field(default_factory=dict)
    #: Profit change split into volume / rate / interaction, with the products
    #: that moved most.
    bridge: dict | None = None
    branch_movers: list[Segment] = field(default_factory=list)
    market: dict[str, MarketPoint] = field(default_factory=dict)
    benchmarks: list[BenchmarkGap] = field(default_factory=list)
    news: list[NewsLine] = field(default_factory=list)
    #: Head office: each product's customer rate against the market's.
    peer_gaps: list[PeerGap] = field(default_factory=list)
    #: Month-end forecasts for this scope's book.
    landings: dict[str, Landing] = field(default_factory=dict)
    #: Which way the policy rate leans at the next meeting (public data).
    policy: PolicyView | None = None
    #: "BR:0101" -> "Dhaka Main (0101)": how placeholders read to a person.
    names: dict[str, str] = field(default_factory=dict)
    #: Moves when any input moves: data version, market, news.
    fingerprint: str = ""

    @property
    def has_book(self) -> bool:
        return self.business_date is not None

    @property
    def data_lag_days(self) -> int | None:
        return (self.today - self.business_date).days if self.business_date else None


def fill(text: str, resolve: Callable[[str, str], str]) -> str:
    """Replace `{KIND:key}` placeholders using `resolve(kind, key)`."""
    return _PLACEHOLDER.sub(lambda m: resolve(m.group(1), m.group(2)), text)


def fill_names(text: str, names: dict[str, str]) -> str:
    """Placeholders to the names a person reads. Unknown ones show the key."""
    return fill(text, lambda k, key: names.get(f"{k}:{key}", key))


def placeholders(text: str) -> set[tuple[str, str]]:
    return {(m.group(1), m.group(2)) for m in _PLACEHOLDER.finditer(text)}

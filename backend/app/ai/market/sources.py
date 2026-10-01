"""Fetchers for the public sources.

Each returns plain data; storing it is `service`'s job.

Clients identify themselves honestly and never imitate a browser. That is a
rule, not a style: bb.org.bd serves its pages to an identified program but
answers a disguised one with a CAPTCHA, and a CAPTCHA is a site saying no.
`bb_page` stops on one and reports it; nothing here tries to get past it.
FRED's edge only answers standard client user agents, so it gets the
library's own.
"""

from __future__ import annotations

import csv
import html
import io
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime

import feedparser
import httpx

UA = "FTP-Intelligence/0.2 (market monitor for a Bangladeshi bank)"
TIMEOUT = 25.0


class SourceError(Exception):
    pass


# --- FRED (St. Louis Fed): global rates and commodities ---------------------- #

def fred(series_id: str, since: date, api_key: str = "",
         transport: httpx.BaseTransport | None = None) -> list[tuple[date, Decimal]]:
    """Daily observations since `since`. Uses the API with a key, else the
    public CSV download that needs none."""
    with httpx.Client(timeout=TIMEOUT, transport=transport) as c:
        try:
            if api_key:
                r = c.get("https://api.stlouisfed.org/fred/series/observations", params={
                    "series_id": series_id, "api_key": api_key, "file_type": "json",
                    "observation_start": since.isoformat()})
                r.raise_for_status()
                rows = [(o["date"], o["value"]) for o in r.json().get("observations", [])]
            else:
                r = c.get("https://fred.stlouisfed.org/graph/fredgraph.csv",
                          params={"id": series_id, "cosd": since.isoformat()})
                r.raise_for_status()
                rd = csv.reader(io.StringIO(r.text))
                next(rd, None)
                rows = [(row[0], row[1]) for row in rd if len(row) >= 2]
        except httpx.HTTPError as exc:
            raise SourceError(f"FRED {series_id}: {exc}") from exc
    out = []
    for d, v in rows:
        if v in ("", "."):
            continue                         # FRED marks holidays with "."
        try:
            out.append((date.fromisoformat(d), Decimal(v)))
        except (ValueError, InvalidOperation):
            continue
    return out


# --- Exchange rates ---------------------------------------------------------- #

def fx_bdt(codes: list[str], transport: httpx.BaseTransport | None = None
           ) -> tuple[date, dict[str, Decimal]]:
    """Taka per unit of each currency, from ExchangeRate-API's open endpoint
    (daily market mid; attribution required: "Rates By Exchange Rate API")."""
    try:
        with httpx.Client(timeout=TIMEOUT, headers={"User-Agent": UA}, transport=transport) as c:
            r = c.get("https://open.er-api.com/v6/latest/USD")
            r.raise_for_status()
            d = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise SourceError(f"exchange rates: {exc}") from exc
    if d.get("result") != "success":
        raise SourceError(f"exchange rates: {d.get('error-type', 'unknown error')}")
    rates = d["rates"]
    bdt = Decimal(str(rates["BDT"]))
    out = {}
    for code in codes:
        per_usd = Decimal(str(rates[code])) if code != "USD" else Decimal(1)
        out[code] = (bdt / per_usd).quantize(Decimal("0.0001"))
    as_of = datetime.fromtimestamp(d["time_last_update_unix"], tz=timezone.utc).date()
    return as_of, out


# --- News feeds -------------------------------------------------------------- #

@dataclass(frozen=True)
class Feed:
    name: str
    url: str
    #: A general-news feed keeps only stories the tagger finds relevant.
    general: bool = False
    region: str = "BD"


FEEDS: tuple[Feed, ...] = (
    Feed("The Daily Star", "https://www.thedailystar.net/business/rss.xml"),
    Feed("The Business Standard", "https://www.tbsnews.net/economy/rss.xml"),
    Feed("Dhaka Tribune", "https://www.dhakatribune.com/feed/business"),
    Feed("Prothom Alo English", "https://en.prothomalo.com/feed", general=True),
    Feed("Google News · Bangladesh Bank",
         "https://news.google.com/rss/search?q=%22Bangladesh+Bank%22&hl=en-BD&gl=BD&ceid=BD:en"),
    Feed("Google News · BD rates",
         "https://news.google.com/rss/search?q=Bangladesh+interest+rate+OR+%22call+money%22"
         "+OR+%22treasury+bill%22&hl=en-BD&gl=BD&ceid=BD:en"),
    Feed("Federal Reserve", "https://www.federalreserve.gov/feeds/press_all.xml", region="GLOBAL"),
    Feed("ECB", "https://www.ecb.europa.eu/rss/press.html", region="GLOBAL"),
    Feed("BIS", "https://www.bis.org/doclist/cbspeeches.rss", general=True, region="GLOBAL"),
)


@dataclass(frozen=True)
class NewsItem:
    feed: str
    source: str
    title: str
    url: str
    published_at: datetime | None
    summary: str
    region: str
    general: bool


_TAG = re.compile(r"<[^>]+>")


def _text(s: str | None) -> str:
    return " ".join(html.unescape(_TAG.sub(" ", s or "")).split())


def _when(e) -> datetime | None:
    for k in ("published", "updated"):
        v = e.get(k)
        if not v:
            continue
        try:
            dt = parsedate_to_datetime(v)
        except (TypeError, ValueError):
            try:
                dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
            except ValueError:
                continue
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


def news(feed: Feed, transport: httpx.BaseTransport | None = None) -> list[NewsItem]:
    try:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=True,
                          headers={"User-Agent": UA}, transport=transport) as c:
            r = c.get(feed.url)
            r.raise_for_status()
    except httpx.HTTPError as exc:
        raise SourceError(f"{feed.name}: {exc}") from exc
    parsed = feedparser.parse(r.content)
    out = []
    for e in parsed.entries[:60]:
        title = _text(e.get("title"))
        link = e.get("link") or ""
        if not title or not link:
            continue
        source = feed.name
        # Google News titles end " - Publisher"; name the publisher instead.
        pub = (e.get("source") or {}).get("title") if isinstance(e.get("source"), dict) else None
        if feed.url.startswith("https://news.google.com") and pub:
            source = pub
            if title.endswith(f" - {pub}"):
                title = title[: -len(pub) - 3]
        out.append(NewsItem(feed.name, source, title[:500], link[:1000], _when(e),
                            _text(e.get("summary"))[:600], feed.region, feed.general))
    return out


# --- Bangladesh Bank --------------------------------------------------------- #

BB_PAGES: dict[str, str] = {
    "call_money": "https://www.bb.org.bd/en/index.php/monetaryactivity/call_money_market",
    "ref_rates": "https://www.bb.org.bd/en/index.php/monetaryactivity/money_market_ref_rate",
    "auctions": "https://www.bb.org.bd/en/index.php/monetaryactivity/treasury",
}


class Blocked(SourceError):
    """The site asked for a human. Respect it: stop, and let a person paste."""


#: Public-data pages: the bank-by-bank rate tables, the industry's monthly
#: averages, and the home page's policy-rate box.
BB_PUBLIC: dict[str, str] = {
    "home": "https://www.bb.org.bd/en/index.php",
    "deposit": "https://www.bb.org.bd/en/index.php/financialactivity/interestdeposit",
    "lending": "https://www.bb.org.bd/en/index.php/financialactivity/interestlending",
    "industry": "https://www.bb.org.bd/en/index.php/econdata/intrate",
}


def bb_html(url: str, transport: httpx.BaseTransport | None = None) -> str:
    """A Bangladesh Bank page's HTML; `Blocked` if it asks for a human."""
    try:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=True,
                          headers={"User-Agent": UA}, transport=transport) as c:
            r = c.get(url)
            r.raise_for_status()
    except httpx.HTTPError as exc:
        raise SourceError(f"Bangladesh Bank: {exc}") from exc
    body = r.text
    if "human visitor" in body or "TSPD" in body or "enable JavaScript to view" in body:
        raise Blocked("Bangladesh Bank asked for human verification; automatic collection "
                      "paused -- paste the page instead")
    return body


def html_text(body: str) -> str:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(body, "html.parser")
    for x in soup(["script", "style", "noscript"]):
        x.decompose()
    return soup.get_text("\n", strip=True)


def bb_page(url: str, transport: httpx.BaseTransport | None = None) -> str:
    """A Bangladesh Bank page as plain text, one cell per line."""
    from bs4 import BeautifulSoup

    body = bb_html(url, transport)
    soup = BeautifulSoup(body, "html.parser")
    for x in soup(["script", "style", "noscript"]):
        x.decompose()
    return soup.get_text("\n", strip=True)


# --- World Bank and IMF: Bangladesh's macro outturns and projections ---------- #

def worldbank(indicator: str, transport: httpx.BaseTransport | None = None):
    """The raw World Bank v2 JSON for one Bangladesh indicator, last 15 years."""
    try:
        with httpx.Client(timeout=TIMEOUT, headers={"User-Agent": UA}, transport=transport) as c:
            r = c.get(f"https://api.worldbank.org/v2/country/BGD/indicator/{indicator}",
                      params={"format": "json", "per_page": 15})
            r.raise_for_status()
            return r.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise SourceError(f"World Bank {indicator}: {exc}") from exc


def imf(indicator: str, transport: httpx.BaseTransport | None = None):
    """The raw IMF DataMapper JSON for one indicator.

    Like FRED, the IMF's edge refuses unfamiliar user agents, so this uses the
    library's own (which still names the client honestly)."""
    try:
        with httpx.Client(timeout=TIMEOUT, transport=transport) as c:
            r = c.get(f"https://www.imf.org/external/datamapper/api/v1/{indicator}/BGD")
            r.raise_for_status()
            return r.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise SourceError(f"IMF {indicator}: {exc}") from exc

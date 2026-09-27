"""Collectors against mocked transports (no network)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal as D

import httpx

from app.ai.market import sources


def test_fred_csv_skips_holidays():
    csv = "observation_date,DGS10\n2026-09-22,5.11\n2026-09-23,.\n2026-09-24,5.18\n"
    t = httpx.MockTransport(lambda req: httpx.Response(200, text=csv))
    assert sources.fred("DGS10", date(2026, 9, 1), transport=t) == [
        (date(2026, 9, 22), D("5.11")), (date(2026, 9, 24), D("5.18"))]


def test_fx_cross_rates_in_taka():
    body = {"result": "success", "time_last_update_unix": 1790467351,
            "rates": {"USD": 1, "BDT": 123.0, "EUR": 0.875, "GBP": 0.75}}
    t = httpx.MockTransport(lambda req: httpx.Response(200, json=body))
    as_of, r = sources.fx_bdt(["USD", "EUR", "GBP"], transport=t)
    assert r == {"USD": D("123.0000"), "EUR": D("140.5714"), "GBP": D("164.0000")}


def test_google_news_titles_name_the_publisher():
    rss = """<?xml version="1.0"?><rss><channel><item>
      <title>Bangladesh Bank keeps rate unchanged at 9.5% - Fibre2Fashion</title>
      <link>https://news.google.com/x</link><pubDate>Thu, 24 Sep 2026 10:00:00 GMT</pubDate>
      <source url="https://fibre2fashion.com">Fibre2Fashion</source></item></channel></rss>"""
    t = httpx.MockTransport(lambda req: httpx.Response(200, text=rss))
    feed = sources.Feed("Google News · Bangladesh Bank", "https://news.google.com/rss/search?q=x")
    [item] = sources.news(feed, transport=t)
    assert item.source == "Fibre2Fashion"
    assert item.title == "Bangladesh Bank keeps rate unchanged at 9.5%"
    assert item.published_at.year == 2026


def test_html_in_feed_titles_is_stripped():
    rss = """<?xml version="1.0"?><rss><channel><item>
      <title>&lt;a href="https://x"&gt;Prime Bank gets loan&lt;/a&gt;</title>
      <link>https://x</link></item></channel></rss>"""
    t = httpx.MockTransport(lambda req: httpx.Response(200, text=rss))
    [item] = sources.news(sources.Feed("The Daily Star", "https://x/rss"), transport=t)
    assert item.title == "Prime Bank gets loan"


def test_bb_page_stops_at_human_verification():
    page = "<html><body>Please enable JavaScript to view the page content. This question is " \
           "for testing whether you are a human visitor</body></html>"
    t = httpx.MockTransport(lambda req: httpx.Response(200, text=page))
    try:
        sources.bb_page(sources.BB_PAGES["call_money"], transport=t)
    except sources.Blocked:
        pass
    else:
        raise AssertionError("a CAPTCHA page must stop collection")


def test_bb_page_identifies_itself_honestly():
    seen = {}

    def handler(req):
        seen["ua"] = req.headers["user-agent"]
        return httpx.Response(200, text="<html><body><table><tr><td>Overnight</td></tr></table></body></html>")

    text = sources.bb_page(sources.BB_PAGES["call_money"], transport=httpx.MockTransport(handler))
    assert "Overnight" in text
    assert seen["ua"].startswith("FTP-Intelligence/") and "Mozilla" not in seen["ua"]

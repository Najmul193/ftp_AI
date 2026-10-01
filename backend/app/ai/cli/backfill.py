"""Give the forecasts history to learn from: `python -m app.ai.cli.backfill`.

- FRED: global rates and Brent, several years back.
- Bangladesh Bank: the call money table, which answers a dated request
  with every trading day since 2016 in one page -- one polite request.
- Bangladesh Bank's public tables and the World Bank / IMF macro series.

Values a person entered are never overwritten. Safe to run again. Never
reaches past AI_MARKET_HISTORY_YEARS, which the prune job enforces.

    python -m app.ai.cli.backfill            # 5 years of FRED, all of call money
    python -m app.ai.cli.backfill --years 10
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta

import httpx

from app.ai.config import ai_settings
from app.ai.market import catalog, sources
from app.ai.market import service as market
from app.ai.public import parse
from app.ai.public import service as public
from app.core.db import session_scope

CALL_MONEY = "https://www.bb.org.bd/en/index.php/monetaryactivity/call_money_market"


def call_money_history(transport: httpx.BaseTransport | None = None) -> list[parse.CallMoneyDay]:
    """The whole call money history: the page lists every day when it is asked
    for a date in its long form."""
    try:
        with httpx.Client(timeout=120, follow_redirects=True,
                          headers={"User-Agent": sources.UA}, transport=transport) as c:
            r = c.post(CALL_MONEY, data={"date_picker": date.today().strftime("%d %B, %Y")})
            r.raise_for_status()
    except httpx.HTTPError as exc:
        raise sources.SourceError(f"Bangladesh Bank call money history: {exc}") from exc
    if "human visitor" in r.text or "TSPD" in r.text:
        raise sources.Blocked("Bangladesh Bank asked for human verification")
    return parse.call_money_history(r.text)


def run(years: int) -> dict:
    out: dict = {"fred": {}, "call_money": None, "public": None, "errors": []}
    # Never further back than the prune job keeps.
    years = min(years, ai_settings().AI_MARKET_HISTORY_YEARS)
    since = date.today() - timedelta(days=365 * years)
    key = ai_settings().FRED_API_KEY
    with session_scope() as db:
        ids = market.sync_catalog(db)
        for s in catalog.SERIES:
            if s.source != "fred":
                continue
            try:
                rows = sources.fred(s.upstream or "", since, key)
                out["fred"][s.code] = sum(market.upsert(
                    db, ids, s.code, d, v, "fred", f"https://fred.stlouisfed.org/series/{s.upstream}")
                    for d, v in rows)
            except sources.SourceError as exc:
                out["errors"].append(str(exc))
    with session_scope() as db:
        ids = market.sync_catalog(db)
        try:
            keep_from = date.today() - timedelta(
                days=365 * ai_settings().AI_MARKET_HISTORY_YEARS)
            days = [d for d in call_money_history() if d.day >= keep_from]
            n = 0
            for d in days:
                for code, v in (("BB_CALL_ON", d.overnight), ("BB_CALL_ON_VOL", d.volume),
                                ("BB_SN_7D", d.short_notice_7d)):
                    if v is None:
                        continue
                    try:
                        n += market.upsert(db, ids, code, d.day, v, "bb_web",
                                           f"{CALL_MONEY} (history)", keep_human=True)
                    except market.HumanValueDiffers:
                        pass
            out["call_money"] = {"days": len(days), "new_or_changed": n,
                                 "from": days[0].day if days else None}
        except sources.SourceError as exc:
            out["errors"].append(str(exc))
    with session_scope() as db:
        out["public"] = public.collect(db, manual=True)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--years", type=int, default=5)
    r = run(ap.parse_args().years)
    print("FRED:", {k: v for k, v in r["fred"].items()})
    print("Call money:", r["call_money"])
    pub = r["public"] or {}
    print("Bangladesh Bank public pages:", (pub.get("bb") or {}).get("pages"))
    print("Macro:", (pub.get("macro") or {}).get("series"))
    for e in r["errors"] + pub.get("errors", []):
        print("  error:", e)


if __name__ == "__main__":
    main()

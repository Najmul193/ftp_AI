"""Keep the forecasts, and score them when the outcome is known.

Every day the job first scores what has come due -- a month's profit once the
month's data is in, a market rate once its date has passed -- then keeps the
day's forecasts. The record answers the only question that makes a forecast
worth reading: how often did the outcome land inside the band?
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import desc, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.ai.context import fact_sheet as sheet
from app.ai.forecast import service as forecast
from app.ai.models import ForecastRecord, MarketObservation, MarketSeries
from app.models import AggDailyBranch

#: Kept this long, then pruned: the record is about recent skill.
KEEP_DAYS = 730
BOOK_TARGETS = ("net_ftp_profit", "deposits", "advances", "cost_of_deposits")
MARKET_TARGETS = ("BB_CALL_ON", "US_FEDFUNDS", "US_UST_10Y", "FX_USDBDT")


def _keep(db: Session, made_on: date, scope_key: str, target: str, target_date: date, unit: str,
          band: dict, confidence: str) -> None:
    stmt = insert(ForecastRecord).values(
        made_on=made_on, scope_key=scope_key, target=target, target_date=target_date, unit=unit,
        p10=band["p10"], p50=band["p50"], p90=band["p90"], confidence=confidence)
    db.execute(stmt.on_conflict_do_update(
        index_elements=["made_on", "scope_key", "target", "target_date"],
        set_={"p10": stmt.excluded.p10, "p50": stmt.excluded.p50, "p90": stmt.excluded.p90,
              "confidence": stmt.excluded.confidence}))


def snapshot(db: Session, today: date | None = None) -> dict:
    today = today or date.today()
    kept = 0
    for scope in sheet.all_scopes(db):
        if scope.filter is None:
            continue
        book = forecast.book_outlook(db, forecast.BookScope(scope.filter, label=scope.label),
                                     BOOK_TARGETS)
        if not book.get("available") or book["month_end"] <= book["latest"]:
            continue
        for m in book["metrics"]:
            mo = m.get("month")
            if not mo:
                continue
            _keep(db, today, scope.key, f"book:{m['metric']}:month", book["month_end"], m["unit"],
                  mo, m["confidence"])
            kept += 1
    mk = forecast.market_outlook(db)
    for s in mk["series"]:
        if s["code"] in MARKET_TARGETS and s.get("in_30d"):
            _keep(db, today, "MARKET", f"market:{s['code']}:30d", s["in_30d"]["date"],
                  "pct" if s["unit"] == "pct" else "num", s["in_30d"], s["confidence"])
            kept += 1
    return {"kept": kept}


def _market_actual(db: Session, code: str, on: date) -> Decimal | None:
    """The value on `on` (or the last before it), once the series has moved past it."""
    q = (select(MarketObservation.obs_date, MarketObservation.value)
         .join(MarketSeries, MarketSeries.id == MarketObservation.series_id)
         .where(MarketSeries.code == code))
    newest = db.execute(q.order_by(desc(MarketObservation.obs_date)).limit(1)).first()
    if newest is None or newest[0] < on:
        return None
    row = db.execute(q.where(MarketObservation.obs_date <= on,
                             MarketObservation.obs_date >= on - timedelta(days=7))
                     .order_by(desc(MarketObservation.obs_date)).limit(1)).first()
    return Decimal(row[1]) if row else None


def _book_actual(db: Session, scope_key: str, metric: str, on: date) -> Decimal | None:
    scope = forecast_scope(db, scope_key)
    if scope is None:
        return None
    q = select(func.max(AggDailyBranch.business_date))
    if not scope.unrestricted:
        q = q.where(AggDailyBranch.branch_id.in_(scope.branch_ids or [-1]))
    latest = db.scalar(q)
    if latest is None or latest < on:
        return None
    rows = forecast._daily(db, forecast.BookScope(scope), on.replace(day=1), on)
    if not rows:
        return None
    if forecast.BOOK_METRICS[metric][2] == "flow":
        return sum((v[metric] or Decimal(0) for _, v in rows), Decimal(0))
    return rows[-1][1].get(metric)


def forecast_scope(db: Session, scope_key: str):
    from app.ai.insights import engine
    s = engine.scope_for(db, scope_key)
    return s.filter if s.key == scope_key else None


def score(db: Session, today: date | None = None) -> dict:
    today = today or date.today()
    due = list(db.scalars(select(ForecastRecord).where(ForecastRecord.actual.is_(None),
                                                       ForecastRecord.target_date <= today)))
    scored = 0
    for r in due:
        kind, what, _ = r.target.split(":", 2)
        actual = (_market_actual(db, what, r.target_date) if kind == "market"
                  else _book_actual(db, r.scope_key, what, r.target_date))
        if actual is not None:
            r.actual, r.scored_at = actual, datetime.now(timezone.utc)
            scored += 1
    return {"due": len(due), "scored": scored}


def run(db: Session) -> dict:
    """The daily job: score what has come due, then keep today's forecasts."""
    s = score(db)
    k = snapshot(db)
    return {"scored": s, "kept": k["kept"]}


def record(db: Session, scope_keys: list[str], days: int = 365) -> dict:
    """Scored forecasts for these scopes, newest first, with the hit rate."""
    since = date.today() - timedelta(days=days)
    rows = list(db.scalars(select(ForecastRecord)
                           .where(ForecastRecord.scope_key.in_(scope_keys + ["MARKET"]),
                                  ForecastRecord.made_on >= since)
                           .order_by(desc(ForecastRecord.target_date), desc(ForecastRecord.made_on))))
    scored = [r for r in rows if r.actual is not None]
    inside = [r for r in scored if r.p10 <= r.actual <= r.p90]

    def err(r: ForecastRecord) -> float | None:
        if r.actual is None:
            return None
        if r.unit == "pct":
            return float(abs(r.actual - r.p50))                     # percentage points
        return float(abs(r.actual - r.p50) / abs(r.actual)) if r.actual else None

    # One line per target and date: the forecast made furthest ahead, which is
    # the hardest test, and the one a reader relied on longest.
    latest_per: dict[tuple, ForecastRecord] = {}
    for r in sorted(rows, key=lambda r: r.made_on):
        latest_per.setdefault((r.scope_key, r.target, r.target_date), r)
    return {
        "summary": {"scored": len(scored), "inside_band": len(inside),
                    "hit_rate": round(len(inside) / len(scored), 3) if scored else None,
                    "pending": len(rows) - len(scored)},
        "items": [{"scope": r.scope_key, "target": r.target, "target_date": r.target_date,
                   "made_on": r.made_on, "unit": r.unit, "p10": r.p10, "p50": r.p50, "p90": r.p90,
                   "actual": r.actual, "inside": (r.p10 <= r.actual <= r.p90) if r.actual is not None
                   else None, "error": err(r), "confidence": r.confidence}
                  for r in latest_per.values()][:100],
    }

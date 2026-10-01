"""Forecasts from the database: the market, the policy rate, the bank's book.

Everything here reads a bounded window (the last few hundred points), so a
forecast costs the same in ten years as it does today. Results are cached
for a few minutes per (what, scope, data version): the page, the brief and
the copilot ask the same question in the same minute.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from statistics import median

import numpy as np
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.ai.context.fact_sheet import fingerprint
from app.ai.copilot import tools
from app.ai.copilot.result import compute
from app.ai.forecast import policy, series
from app.ai.market import catalog
from app.ai.models import Macro, MarketNews, MarketObservation, MarketSeries
from app.ai.public import service as public
from app.domain.scope import ScopeFilter
from app.models import AggDailyBranch
from app.repositories.analytics import AnalyticsRepo

#: The market series the outlook forecasts, in reading order.
MARKET_CODES = ("BB_CALL_ON", "BB_TBILL_91", "BB_TBILL_364", "BB_TBOND_5Y", "FX_USDBDT",
                "BD_IND_DEPOSIT", "BD_IND_ADVANCE", "US_FEDFUNDS", "US_UST_10Y", "BRENT")
HORIZON_DAYS = 90
MARKET_WINDOW_DAYS = 3 * 365
BOOK_WINDOW_DAYS = 400

#: Book measures: (label, unit, kind). kind: stock (a balance), flow (summed
#: over days), rate (% a year).
BOOK_METRICS: dict[str, tuple[str, str, str]] = {
    "deposits": ("Deposits", "bdt", "stock"),
    "advances": ("Advances", "bdt", "stock"),
    "net_ftp_profit": ("Net FTP profit", "bdt", "flow"),
    "cost_of_deposits": ("Cost of deposits", "pct", "rate"),
    "yield_on_advances": ("Yield on advances", "pct", "rate"),
    "nim": ("Net interest margin", "pct", "rate"),
}


# --- a small cache --------------------------------------------------------------- #

_CACHE: dict[tuple, tuple[float, object]] = {}
_LOCK = threading.Lock()
TTL = 600


def _cached(key: tuple, build):
    now = time.monotonic()
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and now - hit[0] < TTL:
            return hit[1]
    val = build()
    with _LOCK:
        if len(_CACHE) > 256:
            _CACHE.clear()
        _CACHE[key] = (now, val)
    return val


def _r(x: float, nd: int) -> Decimal:
    return Decimal(str(round(float(x), nd)))


# --- market ------------------------------------------------------------------------ #

def _history(db: Session, code: str, days: int) -> list[tuple[date, Decimal]]:
    since = date.today() - timedelta(days=days)
    rows = db.execute(select(MarketObservation.obs_date, MarketObservation.value)
                      .join(MarketSeries, MarketSeries.id == MarketObservation.series_id)
                      .where(MarketSeries.code == code, MarketObservation.obs_date >= since)
                      .order_by(MarketObservation.obs_date)).all()
    return [(d, Decimal(v)) for d, v in rows]


def _corridor(db: Session) -> tuple[float | None, float | None]:
    vals = {}
    for code in ("BB_SDF", "BB_SLF"):
        h = _history(db, code, 800)
        vals[code] = float(h[-1][1]) if h else None
    return vals["BB_SDF"], vals["BB_SLF"]


def market_series(db: Session, code: str, horizon_days: int = HORIZON_DAYS) -> dict | None:
    """One market series forecast `horizon_days` ahead, stepping at its own
    pace: daily rates by the trading day, weekly auctions by the auction,
    monthly averages by the month."""
    s = catalog.BY_CODE[code]
    hist = _history(db, code, MARKET_WINDOW_DAYS)
    if len(hist) < 3:
        return None
    dates = [d for d, _ in hist]
    gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
    spacing = max(1, int(median(gaps[-60:])))
    steps = max(1, horizon_days // spacing)
    per_year = max(1, 365 // spacing) if spacing > 1 else 250
    lo = hi = None
    if code == "BB_CALL_ON":
        # Overnight money trades inside the corridor: the forecast may not leave it.
        lo, hi = _corridor(db)
    unit = "rate" if s.unit == "pct" else "amount"
    fc = series.forecast([float(v) for _, v in hist], steps, unit=unit, points_per_year=per_year,
                         lo=lo, hi=hi)
    nd = 4 if s.unit in ("pct", "bdt") else 2
    last = dates[-1]
    fdates = [last + timedelta(days=spacing * (k + 1)) for k in range(steps)]

    def at(days: int) -> dict | None:
        k = min(steps - 1, max(0, round(days / spacing) - 1))
        return {"date": fdates[k], "p10": _r(fc.p10[k], nd), "p50": _r(fc.p50[k], nd),
                "p90": _r(fc.p90[k], nd)}

    return {
        "code": code, "name": s.name, "short": s.short, "unit": s.unit, "category": s.category,
        "last": {"date": last, "value": hist[-1][1]},
        "history": [{"date": d, "value": v} for d, v in hist[-120:]],
        "forecast": [{"date": d, "p10": _r(a, nd), "p50": _r(b, nd), "p90": _r(c, nd)}
                     for d, a, b, c in zip(fdates, fc.p10, fc.p50, fc.p90)],
        "in_30d": at(30), "in_90d": at(horizon_days),
        "confidence": fc.confidence, "confidence_reason": fc.confidence_reason,
        "notes": fc.notes, "spacing_days": spacing, "points": fc.n,
        "backtest": {"mae": fc.backtest.mae, "mape": fc.backtest.mape,
                     "coverage": fc.backtest.coverage, "horizon_steps": fc.backtest.horizon},
        "bounded_by": "the SDF–SLF corridor" if lo is not None else None,
    }


def market_outlook(db: Session) -> dict:
    fp = fingerprint(db, news=False)
    return _cached(("market", fp), lambda: {
        "series": [m for c in MARKET_CODES if (m := market_series(db, c))],
        "horizon_days": HORIZON_DAYS})


# --- policy -------------------------------------------------------------------------- #

def _value_at(hist: list[tuple[date, Decimal]], on: date) -> Decimal | None:
    before = [v for d, v in hist if d <= on]
    return before[-1] if before else None


def policy_inputs(db: Session, today: date | None = None) -> policy.Inputs:
    today = today or date.today()

    def last(code: str, days: int = 400) -> list[tuple[date, Decimal]]:
        return _history(db, code, days)

    repo, slf, sdf = last("BB_POLICY", 800), last("BB_SLF", 800), last("BB_SDF", 800)
    call = last("BB_CALL_ON", 30)
    call_avg = (sum(v for _, v in call[-10:]) / len(call[-10:])).quantize(Decimal("0.01")) \
        if call else None
    t91, t364 = last("BB_TBILL_91", 60), last("BB_TBILL_364", 60)
    fx = last("FX_USDBDT", 60)
    fed = last("US_FEDFUNDS", 120)

    infl = {}
    for r in db.scalars(select(Macro).where(Macro.code == "BD_CPI")):
        infl.setdefault((r.year, r.kind), {})[r.source] = r.value
    year = today.year
    now_val = now_year = None
    for y in (year, year - 1, year - 2):
        got = infl.get((y, "actual"), {}) or infl.get((y, "projection"), {})
        if got:
            now_val, now_year = got.get("worldbank") or got.get("imf"), y
            break
    nxt = (infl.get((year + 1, "projection")) or {}).get("imf")

    since = today - timedelta(days=14)
    when = func.coalesce(MarketNews.published_at, MarketNews.fetched_at)
    sig = db.execute(select(func.coalesce(func.sum(MarketNews.rate_signal), 0), func.count())
                     .where(MarketNews.region == "BD", MarketNews.rate_signal != 0,
                            MarketNews.tags.contains(["policy_rate"]),
                            func.date(when) >= since)).one()
    meta = public.meta(db)
    last_mpc = date.fromisoformat(meta["last_mpc"]) if meta.get("last_mpc") else None
    return policy.Inputs(
        repo=repo[-1][1] if repo else None, slf=slf[-1][1] if slf else None,
        sdf=sdf[-1][1] if sdf else None, last_mpc=last_mpc,
        inflation_now=now_val, inflation_now_year=now_year, inflation_next=nxt,
        call_money=call_avg,
        tbill_91=t91[-1][1] if t91 else None, tbill_364=t364[-1][1] if t364 else None,
        usdbdt_now=fx[-1][1] if fx else None,
        usdbdt_month_ago=_value_at(fx, (fx[-1][0] - timedelta(days=30))) if fx else None,
        news_signal=int(sig[0] or 0), news_count=int(sig[1] or 0),
        fed_now=fed[-1][1] if fed else None,
        fed_quarter_ago=_value_at(fed, fed[-1][0] - timedelta(days=90)) if fed else None,
        today=today)


def policy_outlook(db: Session) -> dict:
    o = policy.assess(policy_inputs(db))
    return {
        "leaning": o.leaning, "score": o.score, "odds": o.odds, "repo": o.repo,
        "next_meeting": o.next_meeting, "next_meeting_basis": o.next_meeting_basis,
        "implied_91d_in_9m": o.implied_91d_in_9m, "missing": o.missing,
        "drivers": [{"key": d.key, "label": d.label, "value": d.value, "score": round(d.score, 3),
                     "weight": d.weight, "push": round(d.push, 3), "explain": d.explain}
                    for d in o.drivers],
        "note": "A reading of the signals, weighted; the odds summarise them and are not a "
                "market price.",
    }


# --- the book --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class BookScope:
    """Whose book: the asker's scope, optionally narrowed (one branch)."""
    scope: ScopeFilter
    where: tools.Where = tools.Where()
    label: str = ""

    def key(self) -> tuple:
        w = self.where.to_dict()
        return (tuple(sorted(self.scope.branch_ids or [])) if not self.scope.unrestricted else "ALL",
                tuple((k, tuple(v) if isinstance(v, list) else v) for k, v in sorted(w.items())))


def _daily(db: Session, bs: BookScope, start: date, end: date) -> list[tuple[date, dict]]:
    a = AnalyticsRepo(db, bs.scope)
    f = bs.where.filters(db, start, end)
    per_day = tools.rollup(a, f, "date")
    return [(d, compute(per_day[d], 1)) for d in sorted(per_day)]


def _landing(fc: series.Forecast, days: list[date], last: date, until: date) -> np.ndarray:
    """Each path's total over the calendar days after `last` up to `until`."""
    cal, paths = series.calendar_fill(days, fc.paths, last, until)
    if not cal:
        return np.zeros(fc.paths.shape[0])
    return paths.sum(axis=1)


def book_outlook(db: Session, bs: BookScope, metrics: tuple[str, ...] = tuple(BOOK_METRICS)) -> dict:
    fp = fingerprint(db, news=False)
    return _cached(("book", bs.key(), metrics, fp), lambda: _book(db, bs, metrics))


def _book(db: Session, bs: BookScope, metrics: tuple[str, ...]) -> dict:
    q = select(func.max(AggDailyBranch.business_date))
    if not bs.scope.unrestricted:
        q = q.where(AggDailyBranch.branch_id.in_(bs.scope.branch_ids or [-1]))
    latest = db.scalar(q)
    if latest is None:
        return {"available": False, "reason": "no bank data loaded yet"}
    rows = _daily(db, bs, latest - timedelta(days=BOOK_WINDOW_DAYS), latest)
    # Fit on business days: Friday and Saturday repeat Thursday.
    biz = [(d, v) for d, v in rows if d.weekday() not in series.WEEKEND]
    month_end = series.month_end(latest)
    quarter_end = series.quarter_end(latest)
    until = max(quarter_end, latest + timedelta(days=HORIZON_DAYS))
    fdays = series.business_days_after(latest, until)
    out_metrics = []
    for m in metrics:
        label, unit, kind = BOOK_METRICS[m]
        pts = [(d, v[m]) for d, v in biz if v.get(m) is not None]
        if len(pts) < 3:
            continue
        y = [float(v) for _, v in pts]
        fc = series.forecast(y, len(fdays), unit="amount" if unit == "bdt" else "rate",
                             points_per_year=250, max_history=300)
        nd = 2 if unit == "bdt" else 4
        cal, p50 = series.calendar_fill(fdays, fc.p50, latest, until)
        _, p10 = series.calendar_fill(fdays, fc.p10, latest, until)
        _, p90 = series.calendar_fill(fdays, fc.p90, latest, until)
        item = {
            "metric": m, "label": label, "unit": unit, "kind": kind,
            "history": [{"date": d, "value": v[m]} for d, v in rows[-90:] if v.get(m) is not None],
            "forecast": [{"date": d, "p10": _r(a, nd), "p50": _r(b, nd), "p90": _r(c, nd)}
                         for d, a, b, c in zip(cal, p10, p50, p90)],
            "last": {"date": pts[-1][0], "value": pts[-1][1]},
            "confidence": fc.confidence, "confidence_reason": fc.confidence_reason,
            "notes": fc.notes, "points": fc.n,
            "backtest": {"mae": fc.backtest.mae, "mape": fc.backtest.mape,
                         "coverage": fc.backtest.coverage},
        }

        def at(d: date) -> dict | None:
            for row in item["forecast"]:
                if row["date"] == d:
                    return {k: row[k] for k in ("date", "p10", "p50", "p90")}
            return None

        if kind == "flow":
            # A month's total: what has been earned so far plus each path's
            # remaining days, banded on the totals.
            mtd = sum((v[m] for d, v in rows if d >= latest.replace(day=1)), Decimal(0))
            tot = _landing(fc, fdays, latest, month_end) + float(mtd)
            qtd = sum((v[m] for d, v in rows
                       if d >= date(latest.year, (latest.month - 1) // 3 * 3 + 1, 1)), Decimal(0))
            qtot = _landing(fc, fdays, latest, quarter_end) + float(qtd)
            prev_start = (latest.replace(day=1) - timedelta(days=1)).replace(day=1)
            prev = [v[m] for d, v in rows if prev_start <= d < latest.replace(day=1)]
            full_prev = len(prev) == (latest.replace(day=1) - prev_start).days
            item["month"] = {"end": month_end, "so_far": mtd,
                             "p10": _r(np.percentile(tot, 10), 2), "p50": _r(np.percentile(tot, 50), 2),
                             "p90": _r(np.percentile(tot, 90), 2),
                             "previous_month": sum(prev, Decimal(0)) if full_prev else None}
            item["quarter"] = {"end": quarter_end, "so_far": qtd,
                               "p10": _r(np.percentile(qtot, 10), 2),
                               "p50": _r(np.percentile(qtot, 50), 2),
                               "p90": _r(np.percentile(qtot, 90), 2)}
        else:
            item["month"] = at(month_end) or ({"date": latest, "p10": pts[-1][1], "p50": pts[-1][1],
                                               "p90": pts[-1][1]} if month_end == latest else None)
            item["quarter"] = at(quarter_end)
            item["in_90d"] = at(latest + timedelta(days=HORIZON_DAYS))
        out_metrics.append(item)
    return {"available": True, "latest": latest, "month_end": month_end,
            "quarter_end": quarter_end, "horizon_end": until, "label": bs.label,
            "days_of_history": len(rows), "metrics": out_metrics}


def latest_obs_date(db: Session, code: str) -> date | None:
    return db.scalar(select(MarketObservation.obs_date).join(MarketSeries)
                     .where(MarketSeries.code == code).order_by(desc(MarketObservation.obs_date))
                     .limit(1))

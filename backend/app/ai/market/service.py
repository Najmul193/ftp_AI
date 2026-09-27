"""Store and read market data; run the collectors.

Collectors run only while AI is switched on: with it off the module is
dormant. Each run is recorded in `ai_job_runs`, and takes a transaction-level
advisory lock so two processes never collect at once.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import date, datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo
from decimal import Decimal

from sqlalchemy import delete, desc, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.ai.config import ai_settings
from app.ai.market import catalog, paste, sources
from app.ai.market.news_tags import tag
from app.ai.market.tenor import curve_rate, infer_tenor_days
from app.ai.models import Brief, Insight, JobRun, MarketNews, MarketObservation, MarketSeries
from app.domain.errors import DomainError
from app.models import AggDailyProduct, Product
from app.repositories.rates import RateBook

log = logging.getLogger(__name__)

DHAKA = ZoneInfo("Asia/Dhaka")
#: Bangladesh Bank is read only in Dhaka business hours, Sunday to Thursday,
#: and not again for a day after it has asked for a human.
BB_HOURS = (dtime(9, 30), dtime(20, 0))
BB_WEEKEND = (4, 5)          # Friday, Saturday
BB_BACKOFF = timedelta(hours=24)


# --- series and observations ------------------------------------------------- #

def sync_catalog(db: Session) -> dict[str, int]:
    """Make the series table match `catalog`; return code -> id."""
    have = {s.code: s for s in db.scalars(select(MarketSeries))}
    for s in catalog.SERIES:
        row = have.get(s.code)
        if row is None:
            row = MarketSeries(code=s.code)
            db.add(row)
        row.name, row.category, row.unit = s.name, s.category, s.unit
        row.source, row.tenor_days = s.source, s.tenor_days
    db.flush()
    return {s.code: s.id for s in db.scalars(select(MarketSeries))}


def upsert(db: Session, ids: dict[str, int], code: str, obs_date: date, value: Decimal,
           source: str, ref: str | None = None, user_id: int | None = None,
           keep_human: bool = False) -> bool:
    """Insert or replace one value. True when it is new or changed.

    `keep_human`: an automatic collector never overwrites a value a person
    entered; it raises `HumanValueDiffers` so the difference is reported.
    """
    sid = ids[code]
    existing = db.scalar(select(MarketObservation)
                         .filter_by(series_id=sid, obs_date=obs_date))
    if existing is not None and keep_human and existing.entered_by is not None:
        if Decimal(existing.value) != Decimal(value):
            raise HumanValueDiffers(code, obs_date, Decimal(existing.value), Decimal(value))
        return False
    if existing is None:
        db.add(MarketObservation(series_id=sid, obs_date=obs_date, value=value, source=source,
                                 source_ref=ref, entered_by=user_id))
        return True
    if Decimal(existing.value) == Decimal(value):
        return False
    existing.previous_value = existing.value
    existing.value, existing.source, existing.source_ref = value, source, ref
    existing.entered_by = user_id
    return True


class HumanValueDiffers(Exception):
    def __init__(self, code: str, on: date, entered: Decimal, published: Decimal):
        super().__init__(f"{code} {on}: entered {entered}, Bangladesh Bank shows {published}")


# --- collectors -------------------------------------------------------------- #

def collect_bb(db: Session, *, manual: bool = False) -> dict:
    """Read Bangladesh Bank's rate pages, politely, and store what they say."""
    now = datetime.now(DHAKA)
    if not manual:
        if now.weekday() in BB_WEEKEND or not (BB_HOURS[0] <= now.time() <= BB_HOURS[1]):
            return {"skipped": "outside Dhaka business hours"}
        blocked_at = db.scalar(select(func.max(JobRun.started_at))
                               .where(JobRun.job == "market_bb", JobRun.status == "blocked"))
        if blocked_at and datetime.now(timezone.utc) - blocked_at < BB_BACKOFF:
            return {"skipped": "paused after Bangladesh Bank asked for human verification"}
    ids = sync_catalog(db)
    out: dict = {"pages": {}, "errors": [], "differences": []}
    for i, (name, url) in enumerate(sources.BB_PAGES.items()):
        if i:
            time.sleep(3)                      # one page at a time, unhurried
        try:
            text_ = sources.bb_page(url)
        except sources.Blocked as exc:
            out["blocked"] = str(exc)
            break
        except sources.SourceError as exc:
            out["errors"].append(str(exc))
            continue
        res = paste.parse(text_)
        saved = 0
        for item in res.items:
            if item.obs_date is None:
                continue
            try:
                saved += upsert(db, ids, item.code, item.obs_date, item.value, "bb_web",
                                f"{url} -- {item.evidence}", keep_human=True)
            except HumanValueDiffers as d:
                out["differences"].append(str(d))
        out["pages"][name] = {"found": len(res.items), "new_or_changed": saved,
                              "warnings": res.warnings}
        if not res.items:
            out["errors"].append(f"{name}: no rates found -- has the page layout changed?")
    return out

def collect_prices(db: Session) -> dict:
    ids = sync_catalog(db)
    out: dict = {"fred": {}, "fx": None, "errors": []}
    since = date.today() - timedelta(days=45)
    key = ai_settings().FRED_API_KEY
    for s in catalog.SERIES:
        if s.source != "fred":
            continue
        try:
            rows = sources.fred(s.upstream or "", since, key)
            out["fred"][s.code] = sum(upsert(db, ids, s.code, d, v, "fred",
                                             f"https://fred.stlouisfed.org/series/{s.upstream}")
                                      for d, v in rows)
        except sources.SourceError as exc:
            out["errors"].append(str(exc))
    fx = [s for s in catalog.SERIES if s.source == "exchangerate_api"]
    try:
        as_of, rates = sources.fx_bdt([s.upstream or "" for s in fx])
        out["fx"] = sum(upsert(db, ids, s.code, as_of, rates[s.upstream or ""], "exchangerate_api",
                               "https://www.exchangerate-api.com") for s in fx)
    except sources.SourceError as exc:
        out["errors"].append(str(exc))
    return out


def title_key(title: str) -> str:
    """The same story from two feeds differs in URL, not in headline."""
    return re.sub(r"[^a-z0-9]+", "", title.lower())[:90]


def collect_news(db: Session) -> dict:
    seen = 0
    added = 0
    errors = []
    since = datetime.now(timezone.utc) - timedelta(days=4)
    known = {title_key(t) for t in db.scalars(select(MarketNews.title)
                                             .where(MarketNews.fetched_at >= since))}
    for feed in sources.FEEDS:
        try:
            items = sources.news(feed)
        except sources.SourceError as exc:
            errors.append(str(exc))
            continue
        for it in items:
            t = tag(it.title, it.summary, it.source)
            if it.general and t.relevance == 0:
                continue
            seen += 1
            k = title_key(it.title)
            if k in known:
                continue
            known.add(k)
            res = db.execute(insert(MarketNews).values(
                feed=it.feed, source=it.source, title=it.title, url=it.url,
                published_at=it.published_at, summary=it.summary or None, region=it.region,
                tags=list(t.tags), impacts=list(t.impacts), rate_signal=t.rate_signal,
                relevance=t.relevance,
            ).on_conflict_do_nothing(index_elements=["url"]).returning(MarketNews.id))
            added += len(res.all())
    return {"seen": seen, "added": added, "errors": errors}


def prune(db: Session) -> dict:
    def gone(stmt) -> int:
        return int(getattr(db.execute(stmt), "rowcount", 0) or 0)

    now = datetime.now(timezone.utc)
    n = gone(delete(MarketNews).where(MarketNews.fetched_at < now - timedelta(days=90)))
    j = gone(delete(JobRun).where(JobRun.started_at < now - timedelta(days=30)))
    i = gone(delete(Insight).where(Insight.status == "resolved",
                                   Insight.resolved_at < now - timedelta(days=180)))
    b = gone(delete(Brief).where(Brief.created_at < now - timedelta(days=90)))
    # The egress log refuses deletes unless this transaction says it is the
    # retention job.
    db.execute(text("SET LOCAL ai.retention_purge = 'on'"))
    days = ai_settings().AI_REQUEST_RETENTION_DAYS
    r = gone(text("DELETE FROM ai_requests WHERE created_at < now() - make_interval(days => :d)")
             .bindparams(d=days))
    return {"news": n, "job_runs": j, "insights": i, "briefs": b, "ai_requests": r}


# --- treasury entries -------------------------------------------------------- #

def save_entries(db: Session, user_id: int, entries: list[dict], source: str) -> int:
    """Save confirmed Bangladesh Bank figures. Only manual series are accepted."""
    ids = sync_catalog(db)
    changed = 0
    for e in entries:
        if e["code"] not in catalog.MANUAL:
            raise ValueError(f"{e['code']} is collected automatically and cannot be entered")
        changed += upsert(db, ids, e["code"], e["obs_date"], Decimal(str(e["value"])), source,
                          e.get("ref"), user_id)
    return changed


# --- reading ----------------------------------------------------------------- #

def latest_observations(db: Session) -> dict[str, list[MarketObservation]]:
    """The last 60 observations of every series, newest first."""
    rn = func.row_number().over(partition_by=MarketObservation.series_id,
                                order_by=desc(MarketObservation.obs_date)).label("rn")
    sub = select(MarketObservation.id, rn).subquery()
    rows = db.execute(select(MarketSeries.code, MarketObservation)
                      .join(MarketObservation, MarketObservation.series_id == MarketSeries.id)
                      .join(sub, sub.c.id == MarketObservation.id)
                      .where(sub.c.rn <= 60)
                      .order_by(MarketSeries.code, desc(MarketObservation.obs_date))).all()
    out: dict[str, list[MarketObservation]] = {}
    for code, obs in rows:
        out.setdefault(code, []).append(obs)
    return out


def _curve(latest: dict[str, list[MarketObservation]]) -> list[dict]:
    """The taka curve: money market for the short end, government paper beyond."""
    order = [("BB_CALL_ON", "BB_DOMMR_ON"), ("BB_DOMMR_1W", "BB_SN_7D"), ("BB_DOMMR_1M",),
             ("BB_TBILL_91",), ("BB_TBILL_182",), ("BB_TBILL_364",), ("BB_TBOND_2Y",),
             ("BB_TBOND_5Y",), ("BB_TBOND_10Y",), ("BB_TBOND_15Y",), ("BB_TBOND_20Y",)]
    pts = []
    for choices in order:
        for code in choices:
            obs = latest.get(code)
            if obs:
                s = catalog.BY_CODE[code]
                pts.append({"code": code, "label": s.short, "tenor_days": s.tenor_days,
                            "value": obs[0].value, "as_of": obs[0].obs_date})
                break
    return pts


def overview(db: Session, *, include_balances: bool) -> dict:
    latest = latest_observations(db)
    today = date.today()
    series = []
    for s in catalog.SERIES:
        obs = latest.get(s.code, [])
        cur = obs[0] if obs else None
        prev = obs[1] if len(obs) > 1 else None
        series.append({
            "code": s.code, "name": s.name, "short": s.short, "category": s.category,
            "unit": s.unit, "source": s.source, "tenor_days": s.tenor_days,
            "value": cur.value if cur else None,
            "as_of": cur.obs_date if cur else None,
            "previous": prev.value if prev else None,
            "previous_as_of": prev.obs_date if prev else None,
            "change": (cur.value - prev.value) if cur and prev else None,
            "stale": cur is None or (today - cur.obs_date).days > s.stale_after_days,
            "spark": [o.value for o in reversed(obs[:30])],
            "entered_by": cur.entered_by if cur else None,
            "source_ref": cur.source_ref if cur else None,
        })
    curve = _curve(latest)
    return {"series": series, "curve": curve,
            "benchmarks": benchmarks(db, curve, include_balances=include_balances)}


def benchmarks(db: Session, curve: list[dict], *, include_balances: bool) -> dict:
    """Each product's configured FTP benchmark against the market at its tenor."""
    try:
        book = RateBook(db, date.today())
    except DomainError as exc:
        return {"items": [], "error": f"rate configuration unavailable: {exc}"}
    pts = [(p["tenor_days"], Decimal(p["value"])) for p in curve if p["tenor_days"]]
    balances: dict[str, Decimal] = {}
    as_of = None
    if include_balances:
        as_of = db.scalar(select(func.max(AggDailyProduct.business_date)))
        if as_of:
            balances = {r.product_code: Decimal(r.asset_balance) + Decimal(r.liability_balance)
                        for r in db.scalars(select(AggDailyProduct)
                                            .where(AggDailyProduct.business_date == as_of))}
    items = []
    for p in db.scalars(select(Product).where(Product.is_active.is_(True))
                        .order_by(Product.side, Product.product_code)):
        side = p.side.value
        nature = p.liability_nature.value if p.liability_nature else None
        days, basis = infer_tenor_days(p.short_name, side, nature)
        try:
            bench = book.resolve(p.product_code).benchmark.value
        except DomainError:
            bench = None
        market = curve_rate(pts, days) if pts else None
        gap_bp = None
        impact = None
        if bench is not None and market is not None:
            gap_bp = int(((Decimal(bench) - market[0]) * 100).to_integral_value())
            bal = balances.get(p.product_code)
            if bal:
                # A month of the gap on today's balance: how much FTP credit or
                # charge is attributed differently from a market-based benchmark.
                impact = (bal * (Decimal(bench) - market[0]) / 100 * 30 / 365).quantize(Decimal(1))
        items.append({
            "product_code": p.product_code, "name": p.short_name, "side": side,
            "tenor_days": days, "tenor_basis": basis, "benchmark": bench,
            "market": market[0] if market else None, "market_basis": market[1] if market else None,
            "gap_bp": gap_bp, "balance": balances.get(p.product_code) if include_balances else None,
            # For non-maturity deposits the gap mostly reflects the bank's own
            # behavioural pricing policy, not an error.
            "behavioural": side == "LIABILITY" and nature == "DEMAND",
            "monthly_impact": impact,
        })
    return {"items": items, "balances_as_of": as_of}


def news(db: Session, *, tag_: str | None, region: str | None, min_relevance: int,
         limit: int, offset: int, q: str | None, sort: str = "top") -> tuple[list[MarketNews], int]:
    when = func.coalesce(MarketNews.published_at, MarketNews.fetched_at)
    stmt = select(MarketNews).where(MarketNews.relevance >= min_relevance)
    if sort == "top":
        # What matters most for an FTP book this week, then the newest.
        stmt = stmt.where(when >= datetime.now(timezone.utc) - timedelta(days=7))
    if tag_:
        stmt = stmt.where(MarketNews.tags.contains([tag_]))
    if region:
        stmt = stmt.where(MarketNews.region == region)
    if q:
        stmt = stmt.where(MarketNews.title.ilike(f"%{q}%"))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    order = (desc(MarketNews.relevance), desc(when)) if sort == "top" else (desc(when),)
    rows = list(db.scalars(stmt.order_by(*order).limit(limit).offset(offset)))
    return rows, total


def series_history(db: Session, code: str, days: int) -> list[MarketObservation]:
    since = date.today() - timedelta(days=days)
    return list(db.scalars(select(MarketObservation)
                           .join(MarketSeries, MarketSeries.id == MarketObservation.series_id)
                           .where(MarketSeries.code == code, MarketObservation.obs_date >= since)
                           .order_by(MarketObservation.obs_date)))

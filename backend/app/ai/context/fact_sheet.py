"""Build the fact sheet for one scope from the platform's own figures.

Reads only the cached, aggregate-grain analytics (`kpis`, `banking_ratios`,
`variance_bridge`): never the account-level fact table, which a small managed
database cannot afford to scan on a schedule. Stored results are shared with
the pages that show the same figures, so a sheet built after an upload mostly
reads what the dashboards already computed.

The scope is resolved here, server-side, exactly as for a user of that scope:
a division's sheet is built with that division's branch set, so nothing from
outside it can appear in its insights or its brief.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.insights.facts import (
    BenchmarkGap, Delta, FactSheet, Landing, MarketPoint, NewsLine, PeerGap, PolicyView, Segment,
)
from app.ai.market import catalog
from app.ai.market import service as market
from app.ai.market.tenor import points_used
from app.ai.models import MarketNews, MarketObservation
from app.domain.scope import ScopeFilter
from app.models import AggDailyBranch, Branch, District, Division, Product
from app.repositories.analytics import AnalyticsRepo
from app.repositories.cache import data_version
from app.repositories.dashboard import Filters

WINDOW_DAYS = 7


@dataclass(frozen=True)
class Scope:
    key: str                         # HO | DIV:<id> | PUBLIC
    label: str
    filter: ScopeFilter | None       # None: no bank data at all

    @property
    def head_office(self) -> bool:
        return self.key == "HO"


HO = Scope("HO", "the whole bank", ScopeFilter(unrestricted=True))
PUBLIC = Scope("PUBLIC", "the market", None)


def division_scope(db: Session, division_id: int) -> Scope | None:
    d = db.get(Division, division_id)
    if d is None:
        return None
    ids = frozenset(db.scalars(select(Branch.id).where(Branch.division_id == division_id)))
    return Scope(f"DIV:{division_id}", f"{d.name} division", ScopeFilter(False, ids))


def all_scopes(db: Session) -> list[Scope]:
    """Head office, every division with branches, and the public market."""
    divs = [d for d in db.scalars(select(Branch.division_id).distinct()) if d is not None]
    return [HO] + [s for d in sorted(divs) if (s := division_scope(db, d))] + [PUBLIC]


def fingerprint(db: Session, *, news: bool = True) -> str:
    """Moves when anything a sheet is built from moves.

    `news=False` leaves stories out: a new headline every quarter of an hour
    does not make the morning's figures out of date.
    """
    obs = db.scalar(select(func.max(func.coalesce(MarketObservation.updated_at,
                                                  MarketObservation.created_at))))
    fp = f"{data_version(db)}|{obs.isoformat() if obs else '-'}"
    if news:
        fp += f"|{db.scalar(select(func.max(MarketNews.id))) or 0}"
    return fp


# --- the parts --------------------------------------------------------------- #

def _d(v) -> Decimal | None:
    return None if v is None else Decimal(str(v))


def market_points(db: Session, today: date) -> dict[str, MarketPoint]:
    latest = market.latest_observations(db)
    out = {}
    for s in catalog.SERIES:
        obs = latest.get(s.code, [])
        cur = obs[0] if obs else None
        prev = obs[1] if len(obs) > 1 else None
        week = None
        if cur is not None:
            week = next((o for o in obs[1:] if o.obs_date <= cur.obs_date - timedelta(days=7)), None)
        out[s.code] = MarketPoint(
            code=s.code, short=s.short, unit=s.unit, category=s.category,
            value=_d(cur.value) if cur else None, as_of=cur.obs_date if cur else None,
            prev=_d(prev.value) if prev else None, prev_as_of=prev.obs_date if prev else None,
            week_ago=_d(week.value) if week else None,
            week_ago_as_of=week.obs_date if week else None,
            stale=cur is None or (today - cur.obs_date).days > s.stale_after_days,
            tenor_days=s.tenor_days, source=cur.source if cur else "")
    return out


def _benchmarks(db: Session, points: dict[str, MarketPoint]) -> list[BenchmarkGap]:
    latest = market.latest_observations(db)
    curve = market.taka_curve(latest)
    as_of_at = {p["tenor_days"]: p["as_of"] for p in curve if p["tenor_days"]}
    rows = market.benchmarks(db, curve, include_balances=True)["items"]
    out = []
    for r in rows:
        used = points_used(list(as_of_at), r["tenor_days"])
        dates = [as_of_at[t] for t in used if as_of_at.get(t)]
        out.append(BenchmarkGap(
            product_code=r["product_code"], name=r["name"], side=r["side"],
            tenor_days=r["tenor_days"], tenor_basis=r["tenor_basis"],
            benchmark=_d(r["benchmark"]), market=_d(r["market"]),
            market_basis=r["market_basis"], gap_bp=r["gap_bp"], balance=_d(r["balance"]),
            monthly_impact=_d(r["monthly_impact"]), behavioural=r["behavioural"],
            market_as_of=min(dates) if dates else None))
    return out


def _news(db: Session) -> list[NewsLine]:
    since = datetime.now(timezone.utc) - timedelta(hours=72)
    when = func.coalesce(MarketNews.published_at, MarketNews.fetched_at)
    rows = db.scalars(select(MarketNews).where(when >= since, MarketNews.relevance >= 3)
                      .order_by(MarketNews.relevance.desc(), when.desc()).limit(8))
    return [NewsLine(n.source, n.title, n.url,
                     n.published_at.isoformat() if n.published_at else None,
                     n.rate_signal, n.relevance, tuple(n.tags or ())) for n in rows]


def _day_before_or_on(db: Session, scope: ScopeFilter, d: date) -> date | None:
    q = select(func.max(AggDailyBranch.business_date)).where(AggDailyBranch.business_date <= d)
    if not scope.unrestricted:
        q = q.where(AggDailyBranch.branch_id.in_(scope.branch_ids or [-1]))
    return db.scalar(q)


def _book(db: Session, a: AnalyticsRepo, fs: FactSheet, latest: date) -> None:
    start = latest - timedelta(days=WINDOW_DAYS - 1)
    prior_end = start - timedelta(days=1)
    win = Filters(date_from=start, date_to=latest)
    prior_start = prior_end - timedelta(days=WINDOW_DAYS - 1)
    prior = Filters(date_from=prior_start, date_to=prior_end)

    k = a.kpis_with_comparison(win)
    cmp_ = k.get("comparison") or {}
    fs.window = (start, latest)
    fs.prior_window = (prior_start, prior_end)
    fs.prior_has_data = bool(cmp_.get("prior_has_data"))
    deltas = cmp_.get("deltas") or {}
    for name, key in (("net", "net_ftp_profit"), ("lending", "asset_ftp_profit"),
                      ("deposit", "liability_ftp_profit")):
        dd = deltas.get(key)
        if dd:
            fs.profit[name] = Delta(_d(dd["current"]), _d(dd["prior"]) if fs.prior_has_data else None)

    now_r = a.banking_ratios(win)
    was_r = a.banking_ratios(prior) if fs.prior_has_data else {}
    for name, key in (("nim", "nim_pct"), ("cost_of_deposits", "cost_of_deposits_pct"),
                      ("yield_on_advances", "yield_on_advances_pct"),
                      ("gross_spread", "gross_spread_pct"), ("ftp_yield", "ftp_yield_pct")):
        fs.ratios[name] = Delta(_d(now_r.get(key)), _d(was_r.get(key)))

    # Stocks are read on single days: a window's balances are summed over its
    # days, which is a balance-day figure, not what the book holds.
    then = _day_before_or_on(db, a.scope, prior_end)
    day_now = a.banking_ratios(Filters(date_from=latest, date_to=latest))
    day_then = a.banking_ratios(Filters(date_from=then, date_to=then)) if then else {}
    for name, key in (("deposits", "deposits"), ("advances", "advances"),
                      ("casa_ratio", "casa_ratio_pct"), ("cd_ratio", "credit_deposit_ratio_pct")):
        fs.book[name] = Delta(_d(day_now.get(key)), _d(day_then.get(key)))

    codes = {p.short_name: p.product_code for p in db.scalars(select(Product))}
    vb = a.variance_bridge(win, by="product")
    if vb.get("available"):
        fs.bridge = {
            "mode": vb["comparison_mode"], "total": vb["total_change"],
            "volume": vb["volume_effect"], "rate": vb["rate_effect"],
            "interaction": vb["interaction_effect"],
            "top": [Segment("product", codes.get(s["label"], s["label"]), s["change"],
                            s["volume_effect"], s["rate_effect"], s["prior_profit"],
                            s["current_profit"]) for s in vb["segments"][:3]],
        }
    br = a.variance_bridge(win, by="branch")
    if br.get("available") and br["comparison_mode"] == "preceding_period":
        fs.branch_movers = [
            Segment("branch", s["label"].split(" ", 1)[0], s["change"], s["volume_effect"],
                    s["rate_effect"], s["prior_profit"], s["current_profit"])
            for s in br["segments"]]


def names(db: Session) -> dict[str, str]:
    names = {f"BR:{b.branch_code}": f"{b.branch_name} ({b.branch_code})"
             for b in db.scalars(select(Branch))}
    names.update({f"PRD:{p.product_code}": p.short_name for p in db.scalars(select(Product))})
    names.update({f"DIV:{d.id}": f"{d.name} division" for d in db.scalars(select(Division))})
    names.update({f"DIST:{d.code}": f"{d.name} district" for d in db.scalars(select(District))})
    return names


def build(db: Session, scope: Scope, *, today: date | None = None,
          fp: str | None = None) -> FactSheet:
    today = today or date.today()
    fs = FactSheet(scope_key=scope.key, scope_label=scope.label, today=today,
                   fingerprint=fp or fingerprint(db))
    fs.market = market_points(db, today)
    fs.news = _news(db)
    fs.names = names(db)
    if scope.filter is not None:
        a = AnalyticsRepo(db, scope.filter)
        latest = a.dash.latest_business_date()
        if latest is not None:
            fs.business_date = latest
            _book(db, a, fs, latest)
    if scope.head_office:
        fs.benchmarks = _benchmarks(db, fs.market)
    _outlook(db, scope, fs)
    return fs


def _outlook(db: Session, scope: Scope, fs: FactSheet) -> None:
    """Forecasts and the market's posted rates: what the detectors look ahead with.

    Imported here, not at the top: the forecasts read this module's fingerprint."""
    from app.ai.forecast import service as forecast
    from app.ai.public import service as public

    try:
        o = forecast.policy_outlook(db)
        drivers = sorted(o["drivers"], key=lambda d: -abs(d["push"]))[:2]
        fs.policy = PolicyView(o["leaning"], o["odds"], o["next_meeting"], o["repo"],
                               tuple(d["explain"] for d in drivers))
    except Exception:  # noqa: BLE001 - an outlook must never stop the feed
        fs.policy = None
    if scope.filter is not None and fs.has_book:
        try:
            book = forecast.book_outlook(db, forecast.BookScope(scope.filter, label=scope.label),
                                         ("net_ftp_profit", "deposits", "advances"))
        except Exception:  # noqa: BLE001
            book = {"available": False}
        for m in book.get("metrics", []) if book.get("available") else []:
            mo = m.get("month")
            if not mo:
                continue
            fs.landings[m["metric"]] = Landing(
                m["metric"], m["label"], m["unit"], m["kind"], book["month_end"],
                _d(m["last"]["value"]), _d(mo["p10"]), _d(mo["p50"]), _d(mo["p90"]),
                _d(mo.get("so_far")), _d(mo.get("previous_month")), m["confidence"])
    try:
        from app.ai.public import explorer
        comp = explorer.movers(db, "competitors", limit=12)
        fs.rate_moves = comp["items"] or explorer.movers(db, "pcb", limit=6)["items"]
    except Exception:  # noqa: BLE001
        fs.rate_moves = []
    if scope.head_office:
        fs.peer_gaps = [PeerGap(r["product_code"], r["side"], r["peer_label"], r["our_rate"],
                                r["market_median"], r["pcb_median"], r["p25"], r["p75"],
                                r["balance"], r["month"])
                        for r in public.book_vs_peers(db)]

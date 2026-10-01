"""Collect and read public data: peer bank rates, the industry, the macro outlook.

- Bangladesh Bank's home page gives the policy corridor (repo, SLF, SDF) and
  the date of the last MPC meeting. These replace typing the policy rate in.
- Its bank-by-bank tables give every bank's posted deposit and lending rates
  for the month: where this bank's pricing stands in the market.
- Its interest-rate page gives the industry's weighted average deposit and
  lending rates: the yardstick for this bank's cost of deposits and yield.
- The World Bank and the IMF give Bangladesh's inflation, growth, remittances
  and reserves, the IMF with projections: what drives the policy rate next.

Bangladesh Bank is read under the same rules as the rate pages
(`market.service.bb_gate`): business hours on a schedule, stop on a CAPTCHA.
"""

from __future__ import annotations

import time
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import desc, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.ai.market import catalog, sources
from app.ai.market import service as market
from app.ai.models import Macro, MarketObservation, MarketSeries, PeerFinancial, PeerRate
from app.ai.public import banks, match, parse
from app.models import AggDailyBranchProduct, Product, SystemSetting
from app.services import audit

SETTINGS_KEY = "ai_public"
#: The bank this platform serves, as Bangladesh Bank's tables spell it.
DEFAULT_SELF = "NRBBL"


# --- settings ------------------------------------------------------------------ #

def meta(db: Session) -> dict:
    row = db.scalar(select(SystemSetting).filter_by(key=SETTINGS_KEY))
    v = dict((row.value if row else None) or {})
    v.setdefault("self_bank", DEFAULT_SELF)
    return v


def _set_meta(db: Session, **kw) -> dict:
    row = db.scalar(select(SystemSetting).filter_by(key=SETTINGS_KEY))
    v = dict((row.value if row else None) or {})
    v.update({k: x for k, x in kw.items()})
    if row is None:
        db.add(SystemSetting(key=SETTINGS_KEY, value=v,
                             description="AI module: public data -- this bank's code in "
                                         "Bangladesh Bank's tables, last MPC date"))
    else:
        row.value = v
    db.flush()
    return v


def directory(db: Session) -> dict[str, banks.Bank]:
    """The bank directory with today's full names from Bangladesh Bank's list."""
    return banks.with_names(meta(db).get("bank_names"))


def product_map(db: Session) -> dict[str, str]:
    """Head office's choices: product code -> "book:category" or "none"."""
    return dict(meta(db).get("product_map") or {})


def category_of(p: Product, overrides: dict[str, str]) -> tuple[tuple[str, str] | None, str]:
    """((book, category) or None, "set" | "auto") for one of our products."""
    v = overrides.get(p.product_code)
    if v == "none":
        return None, "set"
    if v and ":" in v:
        book, cat = v.split(":", 1)
        if cat in parse.PRODUCT_LABELS:
            return (book, cat), "set"
    return match.peer_key(p.short_name, p.side.value,
                          p.liability_nature.value if p.liability_nature else None), "auto"


def set_product_map(db: Session, code: str, value: str | None, *, actor_id: int,
                    actor_username: str) -> dict:
    """Set (or with None, clear back to automatic) one product's market category."""
    if value is not None and value != "none":
        book, _, cat = value.partition(":")
        valid = parse.DEPOSIT_PRODUCTS if book == "deposit" else parse.LENDING_PRODUCTS \
            if book == "lending" else ()
        if cat not in valid:
            raise ValueError(f"unknown market category {value!r}")
    before = product_map(db)
    after = dict(before)
    if value is None:
        after.pop(code, None)
    else:
        after[code] = value
    _set_meta(db, product_map=after)
    audit.record(db, action="AI_PRODUCT_MARKET_MAP", entity_type="product", entity_id=code,
                 before={"market_category": before.get(code, "auto")},
                 after={"market_category": after.get(code, "auto")},
                 actor_user_id=actor_id, actor_username=actor_username)
    return after


def competitors(db: Session) -> list[str]:
    return list(meta(db).get("competitors") or [])


def set_competitors(db: Session, codes: list[str], *, actor_id: int, actor_username: str) -> list[str]:
    known = set(banks.BY_CODE)
    bad = [c for c in codes if c not in known]
    if bad:
        raise ValueError(f"unknown bank {bad[0]!r}")
    before = competitors(db)
    after = sorted(dict.fromkeys(codes))
    _set_meta(db, competitors=after)
    audit.record(db, action="AI_COMPETITORS_SET", entity_type="ai_settings", entity_id=SETTINGS_KEY,
                 before={"competitors": before}, after={"competitors": after},
                 actor_user_id=actor_id, actor_username=actor_username)
    return after


#: Peer sets a comparison can use.
PEER_SETS = ("competitors", "pcb", "fb", "scb", "islamic", "all")


def peer_banks(db: Session, peers: str = "competitors") -> tuple[set[str] | None, str]:
    """(bank codes, label) for a peer set; None means every bank. "competitors"
    falls back to private banks while head office has named none."""
    d = banks.BY_CODE
    if peers == "competitors":
        comp = competitors(db)
        if comp:
            return set(comp), "your competitors"
        peers = "pcb"
    if peers == "pcb":
        return {c for c, b in d.items() if b.group == "PCB"}, "private banks"
    if peers == "fb":
        return {c for c, b in d.items() if b.group == "FB"}, "foreign banks"
    if peers == "scb":
        return {c for c, b in d.items() if b.group in ("SCB", "DFI")}, "state-owned and specialised banks"
    if peers == "islamic":
        return {c for c, b in d.items() if b.islamic}, "Islamic banks"
    return None, "all banks"


def set_self_bank(db: Session, bank: str, *, actor_id: int, actor_username: str) -> dict:
    before = meta(db)
    after = _set_meta(db, self_bank=bank.strip().upper())
    audit.record(db, action="AI_PEER_SELF_SET", entity_type="ai_settings", entity_id=SETTINGS_KEY,
                 before={"self_bank": before.get("self_bank")},
                 after={"self_bank": after["self_bank"]},
                 actor_user_id=actor_id, actor_username=actor_username)
    return after


# --- collecting ------------------------------------------------------------------ #

def _store_peer_rates(db: Session, rows: list[parse.PeerRateRow], ref: str) -> int:
    n = 0
    for r in rows:
        stmt = insert(PeerRate).values(month=r.month, bank=r.bank, bank_group=r.group, book=r.book,
                                       product=r.product, rate_low=r.low, rate_high=r.high,
                                       source_ref=ref)
        db.execute(stmt.on_conflict_do_update(
            index_elements=["month", "bank", "book", "product"],
            set_={"rate_low": stmt.excluded.rate_low, "rate_high": stmt.excluded.rate_high,
                  "bank_group": stmt.excluded.bank_group, "source_ref": stmt.excluded.source_ref}))
        n += 1
    return n


def _store_macro(db: Session, code: str, source: str, rows: list[tuple[int, Decimal]],
                 this_year: int, scale: Decimal = Decimal(1)) -> int:
    for year, value in rows:
        kind = "projection" if source == "imf" and year >= this_year else "actual"
        stmt = insert(Macro).values(code=code, year=year, value=value * scale, source=source,
                                    kind=kind)
        db.execute(stmt.on_conflict_do_update(
            index_elements=["code", "year", "source"],
            set_={"value": stmt.excluded.value, "kind": stmt.excluded.kind,
                  "fetched_at": func.now()}))
    return len(rows)


def collect_bb_public(db: Session, *, manual: bool = False) -> dict:
    why_not = market.bb_gate(db, manual=manual)
    if why_not:
        return {"skipped": why_not}
    ids = market.sync_catalog(db)
    out: dict = {"pages": {}, "errors": [], "differences": []}
    for i, (name, url) in enumerate(sources.BB_PUBLIC.items()):
        if i:
            time.sleep(3)                      # one page at a time, unhurried
        try:
            body = sources.bb_html(url)
        except sources.Blocked as exc:
            out["blocked"] = str(exc)
            break
        except sources.SourceError as exc:
            out["errors"].append(str(exc))
            continue
        try:
            if name == "home":
                pr = parse.policy_rates(sources.html_text(body))
                saved = 0
                if pr.as_of:
                    for code, v in (("BB_POLICY", pr.repo), ("BB_SLF", pr.slf), ("BB_SDF", pr.sdf)):
                        if v is None:
                            continue
                        try:
                            saved += market.upsert(db, ids, code, pr.as_of, v, "bb_web",
                                                   f"{url} -- policy rates box", keep_human=True)
                        except market.HumanValueDiffers as d:
                            out["differences"].append(str(d))
                if pr.last_mpc:
                    _set_meta(db, last_mpc=pr.last_mpc.isoformat())
                out["pages"][name] = {"policy_as_of": pr.as_of, "new_or_changed": saved,
                                      "last_mpc": pr.last_mpc}
                if pr.repo is None:
                    out["errors"].append("home: policy rate box not found -- has the page changed?")
            elif name in ("deposit", "lending"):
                if name == "deposit":
                    # The page carries Bangladesh Bank's own bank list: names
                    # follow a bank that renames, with no extra request.
                    names = banks.parse_bank_list(body)
                    if names:
                        _set_meta(db, bank_names=names)
                t = parse.deposit_table(body) if name == "deposit" else parse.lending_table(body)
                n = _store_peer_rates(db, t.rows, url)
                out["pages"][name] = {"month": t.month, "rates": n,
                                      "banks": len({r.bank for r in t.rows}),
                                      "warnings": t.warnings}
                if not t.rows:
                    out["errors"].append(f"{name}: no rates found -- has the table changed?")
            else:
                rows = parse.industry_rates(body)
                saved = 0
                for r in rows:
                    for code, v in (("BD_IND_DEPOSIT", r.deposit_rate),
                                    ("BD_IND_ADVANCE", r.advance_rate),
                                    ("BD_IND_SPREAD", r.spread)):
                        if v is not None:
                            saved += market.upsert(db, ids, code, r.month_end, v, "bb_web", url)
                out["pages"][name] = {"months": len(rows), "new_or_changed": saved}
        except parse.ParseError as exc:
            out["errors"].append(f"{name}: {exc}")
    return out


def collect_macro(db: Session) -> dict:
    out: dict = {"series": {}, "errors": []}
    year = date.today().year
    for m in catalog.MACRO:
        scale = Decimal("1e-9") if m.unit == "usd_bn" else Decimal(1)
        n = 0
        if m.worldbank:
            try:
                n += _store_macro(db, m.code, "worldbank",
                                  parse.worldbank(sources.worldbank(m.worldbank)), year, scale)
            except sources.SourceError as exc:
                out["errors"].append(str(exc))
        if m.imf:
            try:
                n += _store_macro(db, m.code, "imf", parse.imf(sources.imf(m.imf), m.imf),
                                  year, scale)
            except sources.SourceError as exc:
                out["errors"].append(str(exc))
        out["series"][m.code] = n
    return out


def collect(db: Session, *, manual: bool = False) -> dict:
    """The scheduled job: Bangladesh Bank's public pages, then the macro data."""
    bb = collect_bb_public(db, manual=manual)
    mac = collect_macro(db)
    out = {"bb": bb, "macro": mac,
           "errors": [*bb.get("errors", []), *mac.get("errors", [])]}
    if bb.get("blocked"):
        out["blocked"] = bb["blocked"]
    return out


# --- peer financials, entered by head office ------------------------------------- #

def save_peer_financials(db: Session, figures: list[parse.PeerFigure], *, user_id: int,
                         ref: str | None) -> int:
    for f in figures:
        stmt = insert(PeerFinancial).values(bank=f.bank, period_end=f.period_end, metric=f.metric,
                                            value=f.value, source_ref=ref, entered_by=user_id)
        db.execute(stmt.on_conflict_do_update(
            index_elements=["bank", "period_end", "metric"],
            set_={"value": stmt.excluded.value, "source_ref": stmt.excluded.source_ref,
                  "entered_by": stmt.excluded.entered_by}))
    return len(figures)


# --- reading ----------------------------------------------------------------------- #

def latest_month(db: Session, book: str) -> date | None:
    return db.scalar(select(func.max(PeerRate.month)).where(PeerRate.book == book))


def peer_table(db: Session, book: str, month: date | None = None,
               peers: set[str] | None = None) -> dict:
    """Every bank's mid rate per product for a month, with standings, and the
    median of `peers` (a chosen set of banks) where one is given."""
    month = month or latest_month(db, book)
    if month is None:
        return {"month": None, "book": book, "products": [], "banks": []}
    self_bank = meta(db)["self_bank"]
    rows = list(db.scalars(select(PeerRate).where(PeerRate.book == book, PeerRate.month == month)))
    groups: dict[str, str] = {}
    by_product: dict[str, dict[str, Decimal]] = {}
    ranges: dict[tuple[str, str], tuple[Decimal, Decimal]] = {}
    for r in rows:
        groups[r.bank] = r.bank_group
        by_product.setdefault(r.product, {})[r.bank] = match.mid(r.rate_low, r.rate_high)
        ranges[(r.bank, r.product)] = (r.rate_low, r.rate_high)
    order = parse.DEPOSIT_PRODUCTS if book == "deposit" else parse.LENDING_PRODUCTS
    products = []
    for p in order:
        vals = by_product.get(p)
        if not vals:
            continue
        st = match.standing(vals, self_bank, higher_first=book == "deposit")
        # Private commercial banks are this bank's real competitors.
        pcb = {b: v for b, v in vals.items() if groups.get(b) == "PCB"}
        chosen = [v for b, v in vals.items() if peers is not None and b in peers]
        products.append({"product": p, "label": parse.PRODUCT_LABELS[p], **st,
                         "pcb_median": match.quantile(list(pcb.values()), 0.5),
                         "peer_median": match.quantile(chosen, 0.5) if chosen else None,
                         "self_range": ranges.get((self_bank, p))})
    banks = sorted(groups)
    grid = {b: {p: by_product.get(p, {}).get(b) for p in order} for b in banks}
    return {"month": month, "book": book, "self_bank": self_bank, "products": products,
            "banks": [{"bank": b, "group": groups[b], "rates": grid[b]} for b in banks]}


def book_vs_peers(db: Session, *, as_of: date | None = None,
                  branch_ids: list[int] | None = None, include_new: bool = False) -> list[dict]:
    """Each of this bank's products: what its customers actually get (the
    balance-weighted rate from the book) against what the market posts for
    the like product, and the balance that pricing gap sits on.

    `branch_ids` limits the book to a part of the bank (a division's, a
    branch's own); None is the whole bank. `include_new` keeps products with
    no balances yet (just launched): their market range is the launch guide.
    Products are read afresh on every call, so one added or retired in the
    product master shows up, or drops out, at once."""
    m = AggDailyBranchProduct
    q = select(func.max(m.business_date))
    if branch_ids is not None:
        q = q.where(m.branch_id.in_(branch_ids or [-1]))
    as_of = as_of or db.scalar(q)
    ours: dict[str, tuple[Decimal, Decimal | None]] = {}
    if as_of is not None:
        stmt = (select(m.product_code, func.sum(m.total_balance), func.sum(m.roi_x_balance))
                .where(m.business_date == as_of).group_by(m.product_code))
        if branch_ids is not None:
            stmt = stmt.where(m.branch_id.in_(branch_ids or [-1]))
        ours = {code: (Decimal(bal or 0), (Decimal(rx) / Decimal(bal)).quantize(Decimal("0.01"))
                       if bal else None) for code, bal, rx in db.execute(stmt).all()}
    elif not include_new:
        return []
    comp, comp_label = peer_banks(db, "competitors")
    tables = {b: peer_table(db, b, peers=comp) for b in ("deposit", "lending")}
    overrides = product_map(db)
    out = []
    for p in db.scalars(select(Product).where(Product.is_active.is_(True))
                        .order_by(Product.side, Product.product_code)):
        key, source = category_of(p, overrides)
        bal, rate = ours.get(p.product_code, (Decimal(0), None))
        if key is None or (rate is None and not include_new):
            continue
        book, prod = key
        st = next((x for x in tables[book]["products"] if x["product"] == prod), None)
        if st is None or st["median"] is None:
            continue
        ref = st["peer_median"] if st.get("peer_median") is not None else st["median"]
        out.append({"product_code": p.product_code, "name": p.short_name, "side": p.side.value,
                    "peer_book": book, "peer_product": prod, "mapping": source,
                    "peer_label": parse.PRODUCT_LABELS[prod], "our_rate": rate,
                    "market_median": st["median"], "pcb_median": st["pcb_median"],
                    "peer_median": st.get("peer_median"), "peer_label_set": comp_label,
                    "p25": st["p25"], "p75": st["p75"],
                    "gap": (rate - st["median"]).quantize(Decimal("0.01")) if rate is not None else None,
                    "gap_to_peers": (rate - ref).quantize(Decimal("0.01")) if rate is not None else None,
                    "balance": bal, "new": rate is None,
                    "self_posted": st["self"], "month": tables[book]["month"], "as_of": as_of})
    return out


def industry(db: Session) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for code in ("BD_IND_DEPOSIT", "BD_IND_ADVANCE", "BD_IND_SPREAD"):
        rows = db.scalars(select(MarketObservation).join(MarketSeries)
                          .where(MarketSeries.code == code)
                          .order_by(MarketObservation.obs_date))
        out[code] = [{"date": o.obs_date, "value": o.value} for o in rows]
    return out


def macro(db: Session) -> list[dict]:
    """Each macro series: outturns (World Bank first, IMF where it has none)
    and the IMF's projections."""
    rows = list(db.scalars(select(Macro).order_by(Macro.code, Macro.year)))
    out = []
    for m in catalog.MACRO:
        mine = [r for r in rows if r.code == m.code]
        actual: dict[int, Decimal] = {}
        for r in mine:
            if r.kind == "actual" and (r.source == "worldbank" or r.year not in actual):
                actual[r.year] = r.value
        proj = {r.year: r.value for r in mine if r.kind == "projection"}
        out.append({"code": m.code, "name": m.name, "unit": m.unit,
                    "actual": [{"year": y, "value": v} for y, v in sorted(actual.items())][-10:],
                    "projection": [{"year": y, "value": v} for y, v in sorted(proj.items())][:4]})
    return out


def peer_financials(db: Session) -> dict:
    """The latest quarter each bank reported, metric by metric."""
    rows = list(db.scalars(select(PeerFinancial).order_by(desc(PeerFinancial.period_end))))
    latest: dict[tuple[str, str], PeerFinancial] = {}
    for r in rows:
        latest.setdefault((r.bank, r.metric), r)
    banks: dict[str, dict] = {}
    for (bank, metric), r in latest.items():
        b = banks.setdefault(bank, {"bank": bank, "period_end": r.period_end, "values": {}})
        b["values"][metric] = r.value
        b["period_end"] = max(b["period_end"], r.period_end)
    return {"self_bank": meta(db)["self_bank"], "metrics": parse.PEER_METRICS,
            "banks": sorted(banks.values(), key=lambda b: b["bank"])}


def last_collected(db: Session) -> datetime | None:
    from app.ai.models import JobRun
    return db.scalar(select(func.max(JobRun.finished_at)).where(
        JobRun.job == "public_data", JobRun.status.in_(("ok", "partial"))))

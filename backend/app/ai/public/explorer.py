"""The market rate explorer: every bank's posted rates, read the ways a
treasury or product team asks.

- the grid: banks x categories, our bank's posted row and our book's actual
  rates pinned, the medians, our rank;
- one category: every bank laid out, quartiles, where we sit, the trend;
- one bank: its card against ours;
- movers: who changed a rate since last month;
- search: banks, categories and our products by any name people use.

All of it is Bangladesh Bank's public bank-wise data, except "our book"
rows, which are the asker's own part of the book.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.ai.models import PeerRate
from app.ai.public import banks, match, parse
from app.ai.public import service as public
from app.models import Product

#: A posted rate that moved this much since the month before is news.
MOVE_BP = 25

#: Words people use for each category, for search.
CATEGORY_WORDS: dict[str, tuple[str, ...]] = {
    "savings": ("savings", "sb", "saving account"),
    "snd_lt1cr": ("snd", "short notice", "special notice"),
    "snd_1_25cr": ("snd 1 crore", "snd large"), "snd_25_50cr": (), "snd_50_100cr": (),
    "snd_100cr": ("snd 100 crore",),
    "fd_3m": ("fd 3 month", "fdr 3", "3 month deposit", "term deposit 3", "tdr 3"),
    "fd_6m": ("fd 6 month", "fdr 6", "6 month deposit", "tdr 6"),
    "fd_1y": ("fd 1 year", "fdr 1 year", "1 year deposit", "12 month", "tdr 1 year", "one year fd"),
    "fd_2y": ("fd 2 year", "2 year deposit", "tdr 2"),
    "fd_3y": ("fd 3 year", "3 year deposit", "long term deposit", "tdr 3 year", "dps"),
    "agriculture": ("agri", "farm"), "term_large": ("term loan", "project loan", "corporate term"),
    "term_small": ("sme term", "small term"), "wc_large": ("working capital", "overdraft", "od", "cc"),
    "wc_small": ("sme working capital", "sme overdraft"), "export": ("export", "packing credit"),
    "trade": ("trade", "import", "ltr", "latr", "trust receipt", "lc"),
    "housing": ("home loan", "housing", "mortgage"), "consumer": ("personal loan", "auto loan",
                                                                  "car loan", "consumer"),
    "card": ("credit card", "card"), "nbfi": ("nbfi", "financial institution"), "others": (),
}


def _months(db: Session, book: str, n: int = 12) -> list[date]:
    return list(db.scalars(select(PeerRate.month).where(PeerRate.book == book)
                           .group_by(PeerRate.month).order_by(desc(PeerRate.month)).limit(n)))


def _mids(db: Session, book: str, month: date) -> dict[str, dict[str, tuple[Decimal, Decimal, Decimal]]]:
    """category -> bank -> (low, high, mid)."""
    out: dict[str, dict[str, tuple[Decimal, Decimal, Decimal]]] = {}
    for r in db.scalars(select(PeerRate).where(PeerRate.book == book, PeerRate.month == month)):
        out.setdefault(r.product, {})[r.bank] = (r.rate_low, r.rate_high, match.mid(r.rate_low, r.rate_high))
    return out


def _our_book(db: Session, book: str, branch_ids: list[int] | None) -> dict[str, dict]:
    """category -> our book's balance-weighted customer rate and balance,
    over the products mapped to it."""
    rows = public.book_vs_peers(db, branch_ids=branch_ids)
    acc: dict[str, dict] = {}
    for r in rows:
        if r["peer_book"] != book or r["our_rate"] is None:
            continue
        a = acc.setdefault(r["peer_product"], {"rx": Decimal(0), "bal": Decimal(0), "products": []})
        a["rx"] += Decimal(r["our_rate"]) * Decimal(r["balance"])
        a["bal"] += Decimal(r["balance"])
        a["products"].append(r["name"])
    return {k: {"rate": (a["rx"] / a["bal"]).quantize(Decimal("0.01")) if a["bal"] else None,
                "balance": a["bal"], "products": a["products"]} for k, a in acc.items()}


def grid(db: Session, book: str, peers: str, branch_ids: list[int] | None) -> dict:
    months = _months(db, book)
    if not months:
        return {"book": book, "month": None, "banks": [], "categories": []}
    month = months[0]
    mids = _mids(db, book, month)
    order = [p for p in (parse.DEPOSIT_PRODUCTS if book == "deposit" else parse.LENDING_PRODUCTS)
             if p in mids]
    chosen, label = public.peer_banks(db, peers)
    self_bank = public.meta(db)["self_bank"]
    d = public.directory(db)
    book_rates = _our_book(db, book, branch_ids)
    cats = []
    for p in order:
        vals = {b: v[2] for b, v in mids[p].items()}
        st = match.standing(vals, self_bank, higher_first=book == "deposit")
        in_set = [v for b, v in vals.items() if chosen is None or b in chosen]
        cats.append({"product": p, "label": parse.PRODUCT_LABELS[p], "median": st["median"],
                     "p25": st["p25"], "p75": st["p75"], "rank": st["rank"], "banks": st["banks"],
                     "self": st["self"],
                     "peer_median": match.quantile(in_set, 0.5),
                     "book": book_rates.get(p)})
    bank_codes = sorted({b for v in mids.values() for b in v},
                        key=lambda c: (c != self_bank, chosen is not None and c not in chosen, bank_of(d, c).name))
    rows = [{"code": c, "name": bank_of(d, c).name, "group": bank_of(d, c).group,
             "islamic": bank_of(d, c).islamic, "self": c == self_bank,
             "in_set": chosen is None or c in chosen,
             "rates": {p: (mids[p][c][2] if c in mids.get(p, {}) else None) for p in order}}
            for c in bank_codes]
    return {"book": book, "month": month, "months": months, "peers": peers, "peer_label": label,
            "self_bank": self_bank, "categories": cats, "banks": rows}


def bank_of(d: dict[str, banks.Bank], code: str) -> banks.Bank:
    return banks.bank_of(code, d)


def category(db: Session, book: str, product: str, peers: str, branch_ids: list[int] | None) -> dict:
    months = _months(db, book)
    if not months or product not in parse.PRODUCT_LABELS:
        return {"book": book, "product": product, "available": False}
    mids = _mids(db, book, months[0]).get(product, {})
    chosen, label = public.peer_banks(db, peers)
    self_bank = public.meta(db)["self_bank"]
    d = public.directory(db)
    vals = {b: v[2] for b, v in mids.items()}
    st = match.standing(vals, self_bank, higher_first=book == "deposit")
    rows = sorted(({"code": b, "name": bank_of(d, b).name, "group": bank_of(d, b).group,
                    "low": v[0], "high": v[1], "mid": v[2], "self": b == self_bank,
                    "in_set": chosen is None or b in chosen} for b, v in mids.items()),
                  key=lambda r: r["mid"], reverse=book == "deposit")
    trend = []
    for m in reversed(months):
        mm = _mids(db, book, m).get(product, {})
        if not mm:
            continue
        v = {b: x[2] for b, x in mm.items()}
        trend.append({"month": m, "median": match.quantile(list(v.values()), 0.5),
                      "peer_median": match.quantile([x for b, x in v.items()
                                                     if chosen is None or b in chosen], 0.5),
                      "self": v.get(self_bank)})
    ours = _our_book(db, book, branch_ids).get(product)
    pct = None
    if st["rank"] and st["banks"]:
        pct = round(100 * (st["banks"] - st["rank"]) / max(st["banks"] - 1, 1))
    return {"available": True, "book": book, "product": product,
            "label": parse.PRODUCT_LABELS[product], "month": months[0], "peers": peers,
            "peer_label": label, "standing": {**st, "percentile": pct,
                                              "peer_median": match.quantile(
                                                  [r["mid"] for r in rows if r["in_set"]], 0.5)},
            "banks": rows, "trend": trend, "book_rate": ours, "self_bank": self_bank,
            "higher_is_better_for_customer": book == "deposit"}


def bank(db: Session, code: str, branch_ids: list[int] | None) -> dict:
    d = public.directory(db)
    b = d.get(code)
    if b is None:
        return {"available": False}
    self_bank = public.meta(db)["self_bank"]
    out = {"available": True, "code": b.code, "name": b.name, "group": b.group,
           "group_label": banks.GROUP_LABELS.get(b.group, b.group), "islamic": b.islamic,
           "self_bank": self_bank, "books": {}}
    for book in ("deposit", "lending"):
        months = _months(db, book, 2)
        if not months:
            continue
        mids = _mids(db, book, months[0])
        prev = _mids(db, book, months[1]) if len(months) > 1 else {}
        ours = _our_book(db, book, branch_ids)
        rows = []
        for p in (parse.DEPOSIT_PRODUCTS if book == "deposit" else parse.LENDING_PRODUCTS):
            theirs = mids.get(p, {}).get(code)
            if theirs is None:
                continue
            us = mids.get(p, {}).get(self_bank)
            before = prev.get(p, {}).get(code)
            rows.append({"product": p, "label": parse.PRODUCT_LABELS[p],
                         "low": theirs[0], "high": theirs[1], "mid": theirs[2],
                         "change": (theirs[2] - before[2]) if before else None,
                         "median": match.quantile([v[2] for v in mids[p].values()], 0.5),
                         "our_posted": us[2] if us else None,
                         "our_book": (ours.get(p) or {}).get("rate"),
                         "gap_to_us": (theirs[2] - us[2]) if us else None})
        out["books"][book] = {"month": months[0], "rows": rows}
    return out


def movers(db: Session, peers: str = "all", limit: int = 40) -> dict:
    """Banks whose posted mid rate moved by MOVE_BP or more since last month."""
    chosen, label = public.peer_banks(db, peers)
    d = public.directory(db)
    out = []
    months_seen = None
    for book in ("deposit", "lending"):
        months = _months(db, book, 2)
        if len(months) < 2:
            continue
        months_seen = months
        now, before = _mids(db, book, months[0]), _mids(db, book, months[1])
        for p, banks_now in now.items():
            for b, v in banks_now.items():
                if chosen is not None and b not in chosen:
                    continue
                was = before.get(p, {}).get(b)
                if was is None:
                    continue
                ch = v[2] - was[2]
                if abs(ch * 100) >= MOVE_BP:
                    out.append({"book": book, "product": p, "label": parse.PRODUCT_LABELS[p],
                                "code": b, "name": bank_of(d, b).name, "from": was[2], "to": v[2],
                                "change_bp": int(ch * 100), "month": months[0]})
    out.sort(key=lambda r: -abs(r["change_bp"]))
    return {"available": months_seen is not None, "peer_label": label,
            "months": months_seen, "items": out[:limit],
            "note": None if months_seen else "Movers need two months of Bangladesh Bank's tables; "
                                             "the second arrives with next month's collection."}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", s.lower()).strip()


def search(db: Session, q: str, limit: int = 10) -> list[dict]:
    """Banks, categories and our products matching `q`, best first."""
    q = q.strip()
    if len(q) < 2:
        return []
    hits: list[tuple[float, dict]] = []
    for b, score in banks.search_banks(q, public.directory(db), limit=6):
        hits.append((score, {"type": "bank", "code": b.code, "label": b.name,
                             "detail": banks.GROUP_LABELS.get(b.group, b.group)
                             + (" · Islamic" if b.islamic else "")}))
    nq = _norm(q)
    for p, label in parse.PRODUCT_LABELS.items():
        book = "deposit" if p in parse.DEPOSIT_PRODUCTS else "lending"
        words = [_norm(label), *(_norm(w) for w in CATEGORY_WORDS.get(p, ()))]
        score = 0.0
        for w in words:
            if not w:
                continue
            if nq == w:
                score = 1.0
            elif w.startswith(nq) or nq in w:
                score = max(score, 0.85)
            elif all(t in w for t in nq.split()):
                score = max(score, 0.75)
        if score:
            hits.append((score, {"type": "category", "book": book, "product": p, "label": label,
                                 "detail": "Deposit" if book == "deposit" else "Loan"}))
    for prod in db.scalars(select(Product).where(Product.is_active.is_(True))):
        name = _norm(prod.short_name)
        if nq in name or nq == prod.product_code.lower():
            key, _ = public.category_of(prod, public.product_map(db))
            hits.append((0.8, {"type": "product", "code": prod.product_code, "label": prod.short_name,
                               "book": key[0] if key else None, "product": key[1] if key else None,
                               "detail": f"Our product · {parse.PRODUCT_LABELS[key[1]]}" if key
                               else "Our product · not compared"}))
    hits.sort(key=lambda h: -h[0])
    seen, out = set(), []
    for _, h in hits:
        k = (h["type"], h.get("code") or h.get("product"))
        if k not in seen:
            seen.add(k)
            out.append(h)
    return out[:limit]


def months_available(db: Session) -> int:
    return int(db.scalar(select(func.count(func.distinct(PeerRate.month)))) or 0)

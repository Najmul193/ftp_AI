"""The morning brief, composed by code.

This is the brief everyone gets, every day, whether or not an AI provider is
connected: markets overnight, the book last week, and the decisions for today
ranked by what is at stake. An AI write-up (`engine.narrate`) retells the same
facts in prose, in English or Bangla, and is checked against them; it never
replaces this, it sits beside it.

Lines carry `{BR:code}` placeholders; the API fills them for the reader.

PURE: no I/O.
"""

from __future__ import annotations

from decimal import Decimal

from app.ai.insights.detectors import BELL, Finding, pct, signed_bp, taka
from app.ai.insights.facts import FactSheet

#: Market lines in reading order: the corridor, the short end, bills, the world.
BRIEF_MARKET = ("BB_POLICY", "BB_CALL_ON", "BB_TBILL_91", "BB_TBILL_364", "FX_USDBDT",
                "US_FEDFUNDS", "US_UST_10Y", "BRENT")


def _market_line(fs: FactSheet, code: str) -> str | None:
    p = fs.market.get(code)
    if p is None or p.value is None or p.as_of is None:
        return None
    if p.unit == "pct":
        v = pct(p.value)
    elif p.unit == "bdt":
        v = f"৳{Decimal(p.value):.2f}"
    elif p.unit == "usd":
        v = f"${Decimal(p.value):.2f}"
    else:
        v = f"{Decimal(p.value):.2f}"
    line = f"{p.short} {v}"
    if p.prev is not None and p.prev_as_of:
        d = Decimal(p.value) - Decimal(p.prev)
        if p.unit == "pct":
            line += f", {signed_bp(d)} since {p.prev_as_of:%d %b}" if d else ", unchanged"
        elif p.prev:
            ch = d / abs(Decimal(p.prev)) * 100
            line += f", {'+' if ch > 0 else ''}{ch:.1f}% since {p.prev_as_of:%d %b}"
    else:
        line += f" (as of {p.as_of:%d %b})"
    return line + (" · stale" if p.stale else "")


def _book_lines(fs: FactSheet) -> list[str]:
    out: list[str] = []
    net = fs.profit.get("net")
    if net and net.current is not None and fs.window:
        line = (f"Net FTP profit {taka(net.current)} for "
                f"{fs.window[0]:%d %b}–{fs.window[1]:%d %b}")
        if net.change_pct is not None:
            line += f", {net.change_pct:+.1f}% on the week before"
        if fs.bridge:
            vol, rate = Decimal(fs.bridge["volume"]), Decimal(fs.bridge["rate"])
            line += (f" — volume {'+' if vol >= 0 else ''}{taka(vol)}, "
                     f"rate {'+' if rate >= 0 else ''}{taka(rate)}")
        out.append(line)
    dep, adv, casa = fs.book.get("deposits"), fs.book.get("advances"), fs.book.get("casa_ratio")
    if dep and dep.current is not None:
        line = f"Deposits {taka(dep.current)}"
        if dep.change_pct is not None:
            line += f" ({dep.change_pct:+.1f}% in a week)"
        if adv and adv.current is not None:
            line += f", advances {taka(adv.current)}"
        if casa and casa.current is not None:
            line += f", CASA {Decimal(casa.current):.1f}%"
        out.append(line)
    nim, cod = fs.ratios.get("nim"), fs.ratios.get("cost_of_deposits")
    parts = []
    for label, d in (("NIM", nim), ("cost of deposits", cod)):
        if d and d.current is not None:
            s = f"{label} {pct(d.current)}"
            if d.change is not None:
                s += f" ({signed_bp(d.change)})"
            parts.append(s)
    if parts:
        line = ", ".join(parts)
        out.append(line[0].upper() + line[1:])
    return out


def decisions(findings: list[Finding], limit: int = 3) -> list[Finding]:
    """What someone has to decide today: bell-worthy, or carrying an action."""
    picks = [f for f in findings if f.severity in BELL or f.action]
    return sorted(picks, key=lambda f: f.rank)[:limit]


def headline(fs: FactSheet, findings: list[Finding]) -> str:
    policy = next((f for f in findings if f.kind == "policy_rate"), None)
    if policy:
        return policy.title
    ds = decisions(findings)
    if ds:
        monthly = sum((f.money_at_stake or Decimal(0)) for f in ds if f.money_basis == "per month")
        lead = f"{len(ds)} decision{'s' if len(ds) > 1 else ''} for today"
        if monthly:
            return f"{lead}: about {taka(monthly)} a month rests on them"
        return f"{lead}: {ds[0].title}"
    if fs.has_book:
        return "A steady book: nothing needs a decision today"
    return "Markets steady: nothing needs a decision today"


def compose(fs: FactSheet, findings: list[Finding]) -> dict:
    sections = []
    market = [ln for c in BRIEF_MARKET if (ln := _market_line(fs, c))]
    if market:
        sections.append({"key": "markets", "title": "Markets", "lines": market})
    if fs.has_book:
        book = _book_lines(fs)
        lag = fs.data_lag_days
        if lag and lag > 1:
            book.append(f"Bank data runs to {fs.business_date:%d %b}, {lag} days ago.")
        sections.append({"key": "book", "title": "Our book" if fs.scope_key == "HO"
                         else f"{{DIV:{fs.scope_key.split(':', 1)[1]}}}" if fs.scope_key.startswith("DIV:")
                         else "Our book", "lines": book})
    ds = decisions(findings)
    return {
        "headline": headline(fs, findings),
        "sections": sections,
        "decisions": [{"kind": f.kind, "subject": f.subject, "title": f.title,
                       "severity": f.severity, "money_at_stake": f.money_at_stake,
                       "money_basis": f.money_basis, "action": f.action} for f in ds],
        "news": [{"title": n.title, "source": n.source, "url": n.url,
                  "published_at": n.published_at} for n in fs.news[:3]],
        "as_of": {"today": fs.today, "business_date": fs.business_date,
                  "market": max((p.as_of for p in fs.market.values() if p.as_of), default=None)},
    }

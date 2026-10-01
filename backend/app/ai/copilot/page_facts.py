"""What the page in front of the asker is showing, for the copilot to read.

A question asked on a page is about that page: "why is this low?" on the
pulse, "and if loans pass 90%?" in the scenario lab, "is that likely?" on the
outlook. The page's filters already travel with the question (`chat.
page_context`); this adds what the page *shows*, computed here from the same
services the page uses -- never sent up by the browser as text, so it cannot
carry anything the asker could not see, and it is masked like any result.

Returns two parts: `notes` (plain instructions, no bank figures) and `facts`
(bank figures, masked: sent as a bank segment, so the gateway tiers it).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable

from sqlalchemy.orm import Session

from app.ai.forecast import service as forecast
from app.ai.gateway.generalize import blur_amounts, crore
from app.ai.insights import engine
from app.ai.insights.facts import fill
from app.ai.scenario import engine as scenario_engine
from app.domain.scope import ScopeFilter

#: Pages that have something of their own to say.
PAGES = ("outlook", "alco", "pulse", "scenario", "intel", "market")


@dataclass
class PageFacts:
    notes: list[str] = field(default_factory=list)
    facts: list[str] = field(default_factory=list)


def _money(v) -> str:
    return crore(v) if v is not None else "n/a"


def _policy(db: Session, out: PageFacts) -> None:
    o = forecast.policy_outlook(db)
    out.notes.append(
        f"ON THE PAGE, POLICY OUTLOOK: next MPC meeting expected around {o['next_meeting']}; "
        f"repo {float(o['repo']):.2f}%; signals lean to {o['leaning']} (hike {o['odds']['hike']}%, hold "
        f"{o['odds']['hold']}%, cut {o['odds']['cut']}%). Strongest signals: "
        + "; ".join(f"{d['label']} ({d['value']})"
                    for d in sorted(o["drivers"], key=lambda d: -abs(d["push"]))[:3]) + ".")


def _book(db: Session, scope: ScopeFilter, label: str, out: PageFacts) -> None:
    b = forecast.book_outlook(db, forecast.BookScope(scope, label=label),
                              ("net_ftp_profit", "deposits", "advances", "nim"))
    if not b.get("available"):
        return
    lines = []
    for m in b["metrics"]:
        mo = m.get("month") or {}
        if not mo:
            continue
        if m["unit"] == "bdt":
            lines.append(f"{m['label']}{' this month' if m['kind'] == 'flow' else ' at month-end'}: "
                         f"likely {_money(mo.get('p50'))} (range {_money(mo.get('p10'))} to "
                         f"{_money(mo.get('p90'))}; {m['confidence']} confidence)")
        else:
            lines.append(f"{m['label']} at month-end: likely {float(mo['p50']):.2f}% "
                         f"({m['confidence']} confidence)")
    if lines:
        out.facts.append(f"ON THE PAGE, FORECASTS to {b['month_end']}: " + "; ".join(lines) + ".")


def _pulse(db: Session, scope: ScopeFilter, reader: engine.Reader, label: str,
           resolve: Callable[[str, str], str], out: PageFacts) -> None:
    from app.ai import pulse
    p = pulse.build(db, scope, reader, label)
    if not p.get("available"):
        return
    parts = "; ".join(f"{x['label']} {x['score']}/100 ({x['value']})" for x in p["parts"])
    risks = "; ".join(blur_amounts(fill(r["title"], resolve)) for r in p["risks"])
    out.facts.append(f"ON THE PAGE, BANK PULSE: health {p['score']}/100, grade {p['grade']}. "
                     f"Parts: {parts}." + (f" Top risks: {risks}." if risks else ""))


def _scenario(db: Session, scope: ScopeFilter, settings: dict, out: PageFacts) -> None:
    from app.ai.scenario import service as scenario
    try:
        s = scenario_engine.parse(settings)
    except scenario_engine.ScenarioError:
        return
    plain = {k: v for k, v in s.to_dict().items()
             if v not in ({}, [], None) and v != getattr(scenario_engine.Scenario(), k)}
    out.notes.append(
        "ON THE PAGE, THE SCENARIO BEING EXPLORED (settings): "
        + json.dumps(plain or {"no change": True}, separators=(",", ":"))
        + ". A what-if follow-up ('and if...', 'what about...', 'instead') changes THIS scenario: "
          "use the scenario tool with these settings plus the change asked for.")
    r = scenario.run(db, scope, s)
    if r.get("available"):
        c = r["change"]
        out.facts.append(f"ON THE PAGE, ITS RESULT a month: bank NII {_money(c['bank_nii'])}, "
                         f"branches' FTP profit {_money(c['branch_ftp'])}, treasury "
                         f"{_money(c['treasury'])}, deposits {_money(c['deposits'])}.")


def _findings(db: Session, reader: engine.Reader, resolve, out: PageFacts) -> None:
    feed = [i for i, _ in engine.feed(db, reader, limit=6)]
    if feed:
        out.facts.append("ON THE PAGE, OPEN FINDINGS: " + "; ".join(
            f"{blur_amounts(fill(i.title, resolve))} [{i.severity}]" for i in feed) + ".")


def _market(db: Session, scope: ScopeFilter, sel: dict, out: PageFacts) -> None:
    """The market rate explorer: the book, peer set, rate type and bank open."""
    from app.ai.public import banks as bank_dir
    from app.ai.public import explorer
    from app.ai.public import service as public
    book = "lending" if sel.get("book") == "lending" else "deposit"
    peers = sel.get("peers") if sel.get("peers") in public.PEER_SETS else "competitors"
    branch_ids = None if scope.unrestricted else list(scope.branch_ids or [])
    _, peer_label = public.peer_banks(db, peers)
    note = f"ON THE PAGE, MARKET RATES: other banks' posted {book} rates, compared with {peer_label}"
    product = sel.get("product")
    c: dict = {}
    if product:
        c = explorer.category(db, book, str(product), peers, branch_ids)
        if c.get("available"):
            st = c["standing"]
            note += (f"; the rate type open is {c['label']} ({c['month']:%B %Y}): all banks' median "
                     f"{st['median']}%, {peer_label} median {st['peer_median']}%, our posted rate "
                     f"{st['self']}%, we rank {st['rank']} of {st['banks']}")
            if c.get("book_rate") and c["book_rate"].get("rate") is not None:
                out.facts.append(f"ON THE PAGE, OUR CUSTOMERS GET on {c['label']}: "
                                 f"{c['book_rate']['rate']}% (balance-weighted).")
    code = sel.get("bank")
    hint = ""
    if code:
        b = bank_dir.bank_of(str(code), public.directory(db))
        note += f"; the bank open is {b.name}"
        hint = (f' A question about "this bank", "they", "their" or "them" is about {b.name}: '
                f'use market_rates with "banks": ["{b.name}"].')
    if product:
        hint += (f' A question about "this rate" or "this product" is about '
                 f'"{c["label"] if c.get("available") else product}": put it in "products".')
    out.notes.append(note + "." + hint)


def build(db: Session, page: str | None, *, scope: ScopeFilter, reader: engine.Reader,
          label: str, resolve: Callable[[str, str], str], scenario: dict | None = None,
          market: dict | None = None) -> PageFacts:
    out = PageFacts()
    if page not in PAGES:
        return out
    try:
        if page in ("outlook", "alco"):
            _policy(db, out)
            _book(db, scope, label, out)
        elif page == "pulse":
            _pulse(db, scope, reader, label, resolve, out)
        elif page == "scenario" and scenario:
            _scenario(db, scope, scenario, out)
        elif page == "intel":
            _policy(db, out)
            _findings(db, reader, resolve, out)
        elif page == "market":
            _market(db, scope, market or {}, out)
    except Exception:  # noqa: BLE001 - context is a help, never a reason to fail a question
        pass
    return out

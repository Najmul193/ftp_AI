"""Upload copilot: what went wrong in a file, how to fix it, and what looks odd.

Two checks on one batch, both from the platform's own records and neither
involving an AI provider -- nothing about an upload leaves the bank:

* Every rule that fired, in plain words: what it means, the exact values
  behind it (which branch codes, which products), and where to fix it.
* A day-on-day screen of what the file loaded, branch by branch, against the
  previous business day: a branch that vanished, deposits that halved, a book
  that jumped. The validation rules look at one row at a time; a typo that
  drops a zero from a whole branch passes every one of them.

The screen reads the committed rows of the batch, not the staging table,
which is UNLOGGED and empty after any unclean database restart.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.insights.detectors import taka
from app.models import BankDailyAccountData, Branch, UploadBatch, UploadException


@dataclass(frozen=True)
class RuleHelp:
    title: str
    meaning: str
    fix: str
    link: tuple[str, str] | None = None


_MASTER = ("Master data", "#/admin")
RULES: dict[str, RuleHelp] = {
    "V001": RuleHelp("A required field is empty",
                     "Every row needs a date, branch, account, side, product and balance.",
                     "Fill the empty cells in the source file, then upload it again; it merges "
                     "into the day."),
    "V002": RuleHelp("Side is not A or L",
                     "The side column must say A (asset, a loan) or L (liability, a deposit).",
                     "Correct the side column for these rows and upload again."),
    "V003": RuleHelp("Branch not registered",
                     "The file uses branch codes the platform does not know, or branches marked "
                     "inactive. Their rows were not loaded.",
                     "Add these branches (or reactivate them) in Master data, then upload the "
                     "rejected-rows file; it merges into the day.", _MASTER),
    "V004": RuleHelp("Product not registered",
                     "The file uses product codes that are not in the product master, or are "
                     "inactive.",
                     "Add or reactivate these products in Master data, then upload the "
                     "rejected-rows file.", _MASTER),
    "V005": RuleHelp("Product on the wrong side",
                     "A deposit product appears on a loan row, or the other way round.",
                     "Check the side column, or the product's side in Master data.", _MASTER),
    "V006": RuleHelp("No FTP rate in force",
                     "These products have no benchmark rate for the date, so they cannot be "
                     "priced.",
                     "Set a rate in Rate configuration effective on or before the date, then "
                     "upload the rejected rows.", ("Rate configuration", "#/rates")),
    "V007": RuleHelp("Balance is not a number",
                     "The balance cell holds text or a malformed number.",
                     "Correct the balance cells and upload again."),
    "V008": RuleHelp("No interest rate and no interest amount",
                     "A row needs either its rate of interest or the interest amount, to price "
                     "it.", "Fill the ROI or interest column for these rows."),
    "V009": RuleHelp("Zero balance without a rate",
                     "With a zero balance the rate cannot be worked out from the interest.",
                     "Supply the ROI for these rows."),
    "V010": RuleHelp("Duplicate account",
                     "The same account appears twice for the same day and branch; the second "
                     "copy was rejected.",
                     "Remove the duplicate from the source; if both are real, the account "
                     "numbers are wrong."),
    "V011": RuleHelp("Negative balance",
                     "Loaded, but a balance below zero usually means an overdrawn account or a "
                     "sign error.", "Check these accounts in the core banking system."),
    "V012": RuleHelp("Interest rate outside 0–25%",
                     "Loaded, but a rate outside the usual band is often a typing error "
                     "(e.g. 85 for 8.5).", "Check these rates at source."),
    "V013": RuleHelp("Interest does not match rate × balance",
                     "Loaded; the interest supplied differs from what the rate and balance "
                     "imply by more than the tolerance.",
                     "Usually a mid-period rate change or a day-count difference; check the "
                     "largest cases."),
    "V015": RuleHelp("Accounts missing since the day before",
                     "Accounts present on the previous business day are not in this file.",
                     "Confirm they were closed; if not, the extract is incomplete."),
    "V016": RuleHelp("Balance moved more than 50% in a day",
                     "Loaded, but a large one-day move is worth confirming.",
                     "Check the largest movers against the core banking system."),
    "V017": RuleHelp("Branch row count changed by more than 10%",
                     "A branch has noticeably more or fewer rows than the day before.",
                     "Confirm the branch's extract is complete."),
    "V018": RuleHelp("Account number not in the usual 10-digit shape",
                     "Advisory only: the bank's numbering scheme may simply differ.",
                     "No action needed unless the numbers look truncated."),
    "V019": RuleHelp("Extra columns ignored",
                     "The file has columns the platform does not use.",
                     "No action needed."),
}

_SEV = {"REJECT": 0, "WARN": 1, "INFO": 2}

# Day-on-day thresholds.
BRANCH_MOVE_WARN = Decimal(25)       # % change in a branch's deposits or loans
BRANCH_MOVE_SERIOUS = Decimal(50)
BRANCH_MOVE_MIN = Decimal(10_000_000)  # and at least ৳1 crore, so tiny branches are not noise
BOOK_MOVE_WARN = Decimal(5)
BOOK_MOVE_SERIOUS = Decimal(10)
MISSING_MIN_ACCOUNTS = 10
#: A file with fewer rows than this share of the previous day is a completion
#: file (the rejected rows sent back), not a whole day.
COMPLETION_SHARE = Decimal("0.5")
MAX_DATES = 8


def _explained(db: Session, batch: UploadBatch) -> list[dict]:
    E = UploadException
    rows = db.execute(select(E.rule_code, E.severity, func.count(),
                             func.count(func.distinct(E.source_row_no)))
                      .where(E.batch_id == batch.id)
                      .group_by(E.rule_code, E.severity)).all()
    out = []
    for code, sev, n, distinct_rows in rows:
        sev_name = getattr(sev, "value", sev)
        vals = db.execute(select(E.raw_value, func.count()).where(
            E.batch_id == batch.id, E.rule_code == code, E.raw_value.is_not(None))
            .group_by(E.raw_value).order_by(func.count().desc()).limit(8)).all()
        examples = db.execute(select(E.origin, E.source_row_no, E.message).where(
            E.batch_id == batch.id, E.rule_code == code).order_by(E.source_row_no).limit(3)).all()
        h = RULES.get(code)
        out.append({
            "rule": code, "severity": sev_name, "findings": n, "rows": distinct_rows,
            "title": h.title if h else code, "meaning": h.meaning if h else "",
            "fix": h.fix if h else "", "link": {"label": h.link[0], "href": h.link[1]}
            if h and h.link else None,
            # Branch and product codes worth naming; account numbers only by count.
            "values": [{"value": v, "count": c} for v, c in vals] if code in (
                "V002", "V003", "V004", "V005", "V006", "V017") else [],
            "examples": [{"where": o or (f"row {r}" if r else "whole file"), "message": m}
                         for o, r, m in examples],
        })
    out.sort(key=lambda x: (_SEV.get(x["severity"], 9), -x["findings"]))
    return out


def _totals(db: Session, where) -> dict[tuple[str, str], tuple[Decimal, int]]:
    D = BankDailyAccountData
    return {(b, s): (Decimal(v or 0), n) for b, s, v, n in db.execute(
        select(D.branch_code, D.side, func.sum(D.balance), func.count())
        .where(*where).group_by(D.branch_code, D.side))}


def _pct(now: Decimal, was: Decimal) -> Decimal | None:
    return (now - was) / abs(was) * 100 if was else None


def _screen_day(db: Session, batch: UploadBatch, d: date, names: dict[str, str]) -> dict:
    D = BankDailyAccountData
    prior = db.scalar(select(func.max(D.business_date))
                      .where(D.business_date < d, D.is_current.is_(True)))
    out: dict = {"date": d, "compared_with": prior, "findings": [], "note": None}
    if prior is None:
        out["note"] = "No earlier business day to compare with."
        return out
    now = _totals(db, [D.batch_id == batch.id, D.business_date == d])
    was = _totals(db, [D.business_date == prior, D.is_current.is_(True)])
    out["note"], out["findings"] = compare_days(now, was, prior, names)
    return out


Totals = dict[tuple[str, str], tuple[Decimal, int]]


def compare_days(now: Totals, was: Totals, prior: date, names: dict[str, str]
                 ) -> tuple[str | None, list[dict]]:
    """One day's file against the day before: (note, findings). PURE."""
    out: dict = {"note": None, "findings": []}
    rows_now, rows_was = sum(n for _, n in now.values()), sum(n for _, n in was.values())
    if rows_was and Decimal(rows_now) < Decimal(rows_was) * COMPLETION_SHARE:
        out["note"] = (f"This file has {rows_now:,} rows against {rows_was:,} on {prior:%d %b}: "
                       f"a completion file, so it is not compared branch by branch.")
        return out["note"], []

    f = out["findings"]
    for label, side in (("Deposits", "L"), ("Loans", "A")):
        tot_now = sum((v for (b, s), (v, _) in now.items() if s == side), Decimal(0))
        tot_was = sum((v for (b, s), (v, _) in was.items() if s == side), Decimal(0))
        ch = _pct(tot_now, tot_was)
        if ch is not None and abs(ch) >= BOOK_MOVE_WARN:
            f.append({"severity": "serious" if abs(ch) >= BOOK_MOVE_SERIOUS else "warning",
                      "kind": "book_move", "branch": None,
                      "title": f"{label} for the whole bank {'up' if ch > 0 else 'down'} "
                               f"{abs(ch):.1f}% since {prior:%d %b}",
                      "detail": f"{taka(tot_now)} against {taka(tot_was)}. A whole-book move "
                                f"this size is rare in a day; check the file is complete.",
                      "size": abs(tot_now - tot_was)})

    branches_now = {b for b, _ in now}
    for b in sorted({b for b, _ in was}):
        accounts = sum(n for (bb, _), (_, n) in was.items() if bb == b)
        if b not in branches_now and accounts >= MISSING_MIN_ACCOUNTS:
            f.append({"severity": "serious", "kind": "branch_missing", "branch": b,
                      "title": f"{names.get(b, b)}: no rows in this file",
                      "detail": f"It had {accounts:,} accounts on {prior:%d %b}. If the branch "
                                f"did not close, its extract is missing from the file.",
                      "size": sum(v for (bb, _), (v, _) in was.items() if bb == b)})
    for (b, side), (v_now, _) in now.items():
        v_was = was.get((b, side), (Decimal(0), 0))[0]
        if not v_was:
            continue
        ch = _pct(v_now, v_was)
        if ch is None or abs(ch) < BRANCH_MOVE_WARN or abs(v_now - v_was) < BRANCH_MOVE_MIN:
            continue
        what = "Deposits" if side == "L" else "Loans"
        f.append({"severity": "serious" if abs(ch) >= BRANCH_MOVE_SERIOUS else "warning",
                  "kind": "branch_move", "branch": b,
                  "title": f"{names.get(b, b)}: {what.lower()} {'up' if ch > 0 else 'down'} "
                           f"{abs(ch):.0f}% in a day",
                  "detail": f"{taka(v_now)} against {taka(v_was)} on {prior:%d %b}. "
                            f"A move this size is more often a typing or extract error "
                            f"(a missing zero, a missing product) than business.",
                  "size": abs(v_now - v_was)})
    for b in sorted(branches_now - {bb for bb, _ in was}):
        f.append({"severity": "info", "kind": "branch_new", "branch": b,
                  "title": f"{names.get(b, b)}: appears for the first time",
                  "detail": f"Not in the file for {prior:%d %b}. Expected for a new branch.",
                  "size": Decimal(0)})
    order = {"serious": 0, "warning": 1, "info": 2}
    f.sort(key=lambda x: (order[x["severity"]], -x["size"]))
    return None, f


def review(db: Session, batch_ref: str) -> dict | None:
    batch = db.scalar(select(UploadBatch).where(UploadBatch.batch_ref == batch_ref))
    if batch is None:
        return None
    names = {b.branch_code: f"{b.branch_name} ({b.branch_code})" for b in db.scalars(select(Branch))}
    D = BankDailyAccountData
    dates = list(db.scalars(select(D.business_date).where(D.batch_id == batch.id)
                            .distinct().order_by(D.business_date.desc()).limit(MAX_DATES)))
    days = [_screen_day(db, batch, d, names) for d in sorted(dates)]
    rules = _explained(db, batch)

    serious = sum(1 for d in days for x in d["findings"] if x["severity"] == "serious")
    warnings = sum(1 for d in days for x in d["findings"] if x["severity"] == "warning")
    blocked = [r for r in rules if r["severity"] == "REJECT"]
    if batch.status in ("FAILED", "REJECTED"):
        headline = "Nothing from this file was loaded. The reasons and fixes are below."
    elif serious:
        headline = (f"{serious} serious thing{'s' if serious > 1 else ''} to check before relying "
                    f"on this data.")
    elif blocked:
        headline = (f"Loaded, but {batch.rejected_rows:,} row{'s' if batch.rejected_rows != 1 else ''} "
                    f"were held back; fix the master data and send them again.")
    elif warnings:
        headline = f"Loaded. {warnings} movement{'s' if warnings > 1 else ''} worth a second look."
    elif not dates:
        headline = "This batch has no loaded rows to check."
    else:
        headline = "Loaded cleanly: nothing unusual against the previous business day."
    return {
        "batch_ref": batch.batch_ref, "status": batch.status,
        "headline": headline, "serious": serious, "warnings": warnings,
        "rules": rules, "days": days,
        "screened_dates": len(days), "more_dates": len(dates) >= MAX_DATES,
    }

"""Which way Bangladesh Bank's policy rate is leaning at its next meeting.

Not a black box and not a market price: a weighted reading of the signals a
treasury desk itself watches, each shown with its value and its push.

    inflation against target     (World Bank / IMF; BB's target)
    where inflation is heading   (IMF projection, next year against this)
    call money in the corridor   (call rate against repo; SDF..SLF)
    the T-bill curve             (91-day rate 9 months on, implied by 91D/364D)
    the taka                     (USD/BDT over the last month)
    the news                     (rate headlines, last two weeks)
    the Fed                      (US policy over the last quarter)

Each signal scores -1 (cut) .. +1 (hike); the weighted sum leans one way when
it clears a threshold. The "odds" split that sum into hike/hold/cut shares,
and say so: they are a summary of the signals, not a probability anyone
trades on.

PURE: no I/O.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal


#: Bangladesh Bank's medium-term inflation goal used as the yardstick.
INFLATION_TARGET = Decimal("6.0")
#: What a 3-to-12-month forward usually carries above the 91-day rate with no
#: change of policy expected: lenders want more for tying money up longer.
TERM_PREMIUM = Decimal("0.50")
#: Between meetings: BB's MPC now meets about quarterly.
MEETING_GAP_DAYS = 91

WEIGHTS = {"inflation_gap": 0.30, "inflation_path": 0.20, "corridor": 0.15,
           "curve": 0.15, "taka": 0.10, "news": 0.05, "fed": 0.05}


@dataclass(frozen=True)
class Inputs:
    repo: Decimal | None
    slf: Decimal | None
    sdf: Decimal | None
    last_mpc: date | None
    inflation_now: Decimal | None          # latest outturn or current-year estimate, %
    inflation_now_year: int | None
    inflation_next: Decimal | None         # IMF projection for next year, %
    call_money: Decimal | None             # recent average, %
    tbill_91: Decimal | None
    tbill_364: Decimal | None
    usdbdt_now: Decimal | None
    usdbdt_month_ago: Decimal | None
    news_signal: int                       # sum of headline rate signals, last 14 days
    news_count: int
    fed_now: Decimal | None
    fed_quarter_ago: Decimal | None
    today: date


@dataclass(frozen=True)
class Driver:
    key: str
    label: str
    value: str
    score: float           # -1 cut .. +1 hike
    weight: float
    explain: str

    @property
    def push(self) -> float:
        return self.score * self.weight


@dataclass
class Outlook:
    leaning: str                    # hike | hold | cut
    score: float                    # weighted, -1..+1
    odds: dict[str, int]            # hike / hold / cut, whole percent, sum 100
    next_meeting: date | None
    next_meeting_basis: str
    repo: Decimal | None
    drivers: list[Driver] = field(default_factory=list)
    implied_91d_in_9m: Decimal | None = None
    missing: list[str] = field(default_factory=list)


def _clip(x: float) -> float:
    return max(-1.0, min(1.0, x))


def implied_forward(r_short: Decimal, d_short: int, r_long: Decimal, d_long: int) -> Decimal:
    """The simple-interest forward rate from `d_short` to `d_long` days that
    the two spot rates imply, % a year. Money-market convention, ACT/365."""
    a = 1 + float(r_short) / 100 * d_short / 365
    b = 1 + float(r_long) / 100 * d_long / 365
    fwd = (b / a - 1) * 365 / (d_long - d_short) * 100
    return Decimal(str(round(fwd, 4)))


def next_meeting(last: date | None, today: date) -> tuple[date | None, str]:
    if last is None:
        return None, "no meeting date known yet"
    d = last
    while d <= today:
        d += timedelta(days=MEETING_GAP_DAYS)
    return d, f"about {MEETING_GAP_DAYS // 30} months after the last MPC meeting ({last:%d %b %Y})"


def assess(i: Inputs) -> Outlook:
    drivers: list[Driver] = []
    missing: list[str] = []

    if i.inflation_now is not None:
        gap = float(i.inflation_now - INFLATION_TARGET)
        drivers.append(Driver(
            "inflation_gap", "Inflation against target", f"{i.inflation_now:.1f}% vs {INFLATION_TARGET}%",
            _clip(gap / 3), WEIGHTS["inflation_gap"],
            ("Inflation well above target keeps the policy rate high."
             if gap > 1 else "Inflation near target leaves room to ease."
             if gap < 0.5 else "Inflation is a little above target.")
            + (f" ({i.inflation_now_year})" if i.inflation_now_year else "")))
    else:
        missing.append("inflation")

    if i.inflation_now is not None and i.inflation_next is not None:
        fall = float(i.inflation_next - i.inflation_now)
        drivers.append(Driver(
            "inflation_path", "Where inflation is heading (IMF)",
            f"{i.inflation_now:.1f}% → {i.inflation_next:.1f}% next year",
            _clip(fall / 2), WEIGHTS["inflation_path"],
            "The IMF expects inflation to ease, which usually brings cuts."
            if fall < -0.5 else "The IMF expects inflation to rise." if fall > 0.5
            else "The IMF expects inflation to hold steady."))
    else:
        missing.append("inflation projection")

    if i.call_money is not None and i.repo is not None and i.slf and i.sdf and i.slf > i.sdf:
        half = float(i.slf - i.sdf) / 2
        pos = float(i.call_money - i.repo) / half
        drivers.append(Driver(
            "corridor", "Call money in the corridor",
            f"{i.call_money:.2f}% (repo {i.repo:.2f}%, SDF {i.sdf:.2f}–SLF {i.slf:.2f}%)",
            _clip(pos), WEIGHTS["corridor"],
            "Overnight money trades below the repo rate: liquidity is ample, which "
            "eases pressure to hold rates high." if pos < -0.2 else
            "Overnight money trades above the repo rate: liquidity is tight." if pos > 0.2
            else "Overnight money trades close to the repo rate."))
    else:
        missing.append("call money or corridor")

    fwd = None
    if i.tbill_91 is not None and i.tbill_364 is not None:
        fwd = implied_forward(i.tbill_91, 91, i.tbill_364, 364)
        move = float(fwd - i.tbill_91 - TERM_PREMIUM)
        drivers.append(Driver(
            "curve", "What the T-bill curve expects",
            f"91-day {i.tbill_91:.2f}% now; {fwd:.2f}% implied 3–12 months out "
            f"(about {TERM_PREMIUM:.2f} pp of that is the usual term premium)",
            _clip(move / 1.0), WEIGHTS["curve"],
            "The curve prices lower short rates ahead." if move < -0.15
            else "The curve prices higher short rates ahead." if move > 0.15
            else "The curve prices short rates about where they are."))
    else:
        missing.append("T-bill yields")

    if i.usdbdt_now is not None and i.usdbdt_month_ago:
        chg = float((i.usdbdt_now / i.usdbdt_month_ago - 1) * 100)
        drivers.append(Driver(
            "taka", "The taka against the dollar", f"{chg:+.1f}% in a month (USD/BDT {i.usdbdt_now:.2f})",
            _clip(chg / 2), WEIGHTS["taka"],
            "A weakening taka argues for keeping rates high." if chg > 0.5
            else "The taka is steady." if chg > -0.5 else "A firmer taka eases the pressure."))
    else:
        missing.append("USD/BDT")

    if i.news_count:
        drivers.append(Driver(
            "news", "Rate headlines (2 weeks)", f"{i.news_count} headlines, net signal {i.news_signal:+d}",
            _clip(i.news_signal / 4), WEIGHTS["news"],
            "Headlines lean towards tightening." if i.news_signal > 0
            else "Headlines lean towards easing." if i.news_signal < 0
            else "Headlines are mixed."))
    else:
        missing.append("rate headlines")

    if i.fed_now is not None and i.fed_quarter_ago is not None:
        move = float(i.fed_now - i.fed_quarter_ago)
        drivers.append(Driver(
            "fed", "US policy rate (quarter)", f"{i.fed_quarter_ago:.2f}% → {i.fed_now:.2f}%",
            _clip(move / 0.5), WEIGHTS["fed"],
            "The Fed is easing, which takes pressure off the taka." if move < -0.1
            else "The Fed is tightening." if move > 0.1 else "The Fed is on hold."))
    else:
        missing.append("Fed funds")

    total_w = sum(d.weight for d in drivers) or 1.0
    score = sum(d.push for d in drivers) / total_w
    split = odds(score)
    # The leaning is the likeliest outcome, so the headline and the odds agree.
    leaning = max(split, key=lambda k: (split[k], k == "hold"))
    meet, basis = next_meeting(i.last_mpc, i.today)
    return Outlook(leaning, round(score, 3), split, meet, basis, i.repo, drivers, fwd, missing)


def odds(score: float) -> dict[str, int]:
    """Hike/hold/cut shares from the score: hold dominates near zero, and a
    strong lean moves weight to one side. Whole percent summing to 100."""
    k = 6.0
    hike = 1 / (1 + math.exp(-k * (score - 0.35)))
    cut = 1 / (1 + math.exp(k * (score + 0.35)))
    hold = max(0.0, 1 - hike - cut)
    raw = {"hike": hike, "hold": hold, "cut": cut}
    tot = sum(raw.values())
    pct = {k_: int(round(v / tot * 100)) for k_, v in raw.items()}
    pct["hold"] += 100 - sum(pct.values())
    return pct

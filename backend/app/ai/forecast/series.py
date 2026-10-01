"""Forecast one series: a damped-trend model, bands from its own errors, and
an honest record of how well it would have done.

The model is Holt's linear method with a damped trend: a level and a trend,
each updated as a new value arrives, with the trend fading towards flat the
further ahead it looks. Damping matters for money: a deposit book that grew
for three weeks is not assumed to grow at that pace for a year.

Bands come from simulation, not a formula: each path replays the model with
errors drawn from the ones it actually made (a residual bootstrap), so the
spread widens with the horizon and keeps the data's own shape. P10 and P90
are read from the paths; a total over days (a month's profit) is read from
the paths' totals, which is the only correct way to band a sum.

The backtest refits on shorter histories and checks each forecast against
what then happened. Its error is reported beside every forecast, and the
confidence label follows from it and from how much history there is.

PURE: no I/O. Deterministic: the same inputs give the same bands.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np

#: The parameter grid the fit searches. Small on purpose: these series are
#: short and noisy, and a fine grid only fits the noise.
_ALPHA = (0.1, 0.3, 0.5, 0.8)
_BETA = (0.02, 0.1, 0.3)
_PHI = (0.8, 0.9, 0.98, 1.0)

PATHS = 400
SEED = 20261001


@dataclass(frozen=True)
class Fit:
    alpha: float
    beta: float
    phi: float
    level: float
    trend: float
    #: One-step-ahead errors, oldest first.
    resid: np.ndarray
    method: str


def _run(y: np.ndarray, a: float, b: float, phi: float) -> tuple[float, float, np.ndarray]:
    level, trend = y[0], (y[1] - y[0]) if len(y) > 1 else 0.0
    errs = np.empty(len(y) - 1)
    for i in range(1, len(y)):
        pred = level + phi * trend
        errs[i - 1] = y[i] - pred
        new_level = a * y[i] + (1 - a) * pred
        trend = b * (new_level - level) + (1 - b) * phi * trend
        level = new_level
    return level, trend, errs


def _best(y: np.ndarray, phis) -> tuple:
    best = None
    for phi in phis:
        for a in _ALPHA:
            for b in _BETA:
                lvl, tr, e = _run(y, a, b, phi)
                sse = float(np.dot(e, e))
                if best is None or sse < best[0]:
                    best = (sse, a, b, phi, lvl, tr, e)
    return best


def fit(values, horizon: int = 1) -> Fit:
    """The damped-trend model that forecasts best.

    Level and trend weights are chosen by one-step error. The damping is
    chosen by how well each candidate forecasts `horizon` steps ahead on the
    most recent stretch held out: one-step error cannot tell a trend that
    lasts from one that fades, and that difference is the whole forecast.

    Under eight points there is too little to fit: a flat forecast from the
    last value, with errors from the day-to-day changes."""
    y = np.asarray(values, dtype=float)
    if len(y) < 8:
        diffs = np.diff(y) if len(y) > 1 else np.zeros(1)
        return Fit(1.0, 0.0, 0.0, float(y[-1]), 0.0, diffs - diffs.mean(), "last value (short history)")
    h = max(1, min(horizon, len(y) // 4))
    phis = _PHI
    if len(y) - h >= 8:
        scores = []
        for phi in _PHI:
            _, a, b, _, lvl, tr, _ = _best(y[:-h], (phi,))
            f = Fit(a, b, phi, lvl, tr, np.zeros(1), "")
            scores.append((float(np.mean(np.abs(point(f, h) - y[-h:]))), phi))
        phis = (min(scores)[1],)
    _, a, b, phi, lvl, tr, e = _best(y, phis)
    return Fit(a, b, phi, float(lvl), float(tr), e, "damped trend")


def simulate(f: Fit, steps: int, *, paths: int = PATHS, seed: int = SEED,
             lo: float | None = None, hi: float | None = None, scale: float = 1.0) -> np.ndarray:
    """Future paths, shape (paths, steps): the central forecast plus errors
    drawn from the ones the model actually made, accumulated step by step
    and widened by `scale`. `lo`/`hi` bound every value (a rate corridor).

    The errors accumulate rather than feed back into the trend: fed back,
    a run of large errors becomes a runaway trend, and the band explodes."""
    rng = np.random.default_rng(seed)
    resid = f.resid[-250:] if len(f.resid) else np.zeros(1)
    # Recent errors are the better guide; centre them so the bands do not
    # carry a bias the point forecast does not have.
    resid = (resid - resid.mean()) * scale
    draws = rng.choice(resid, size=(paths, steps))
    out = point(f, steps)[None, :] + np.cumsum(draws, axis=1)
    if lo is not None or hi is not None:
        out = np.clip(out, lo if lo is not None else -np.inf, hi if hi is not None else np.inf)
    return out


def point(f: Fit, steps: int) -> np.ndarray:
    """The central forecast: the model's own path, no errors."""
    damp = np.cumsum(f.phi ** np.arange(1, steps + 1))
    return f.level + damp * f.trend


@dataclass
class Backtest:
    #: Mean absolute error, in the series' units.
    mae: float | None
    #: Mean absolute percentage error, for amounts.
    mape: float | None
    #: Share of actual values that fell inside the P10-P90 band.
    coverage: float | None
    origins: int
    horizon: int
    #: How much the bands must widen (or may narrow) for 80% of the backtest's
    #: actual values to fall inside them.
    band_scale: float = 1.0


def backtest(values, horizon: int, *, origins: int = 5) -> Backtest:
    """Refit on the history up to each of the last few points and forecast
    `horizon` steps on: how far off, and how often inside the band."""
    y = np.asarray(values, dtype=float)
    h = max(1, min(horizon, len(y) // 4))
    errs, pcts, inside, norm = [], [], [], []
    used = 0
    for k in range(origins):
        cut = len(y) - h - k * max(1, h // 2)
        if cut < 8:
            break
        f = fit(y[:cut], h)
        sims = simulate(f, h, paths=200)
        p10, p50, p90 = np.percentile(sims, [10, 50, 90], axis=0)
        actual = y[cut:cut + h]
        errs.extend(np.abs(actual - p50))
        pcts.extend(np.abs(actual - p50) / np.maximum(np.abs(actual), 1e-9))
        inside.extend((actual >= p10) & (actual <= p90))
        half = np.maximum((p90 - p10) / 2, 1e-9)
        norm.extend(np.abs(actual - p50) / half)
        used += 1
    if not used:
        return Backtest(None, None, None, 0, h)
    # Calibrate both ways: widen bands that missed, narrow ones that were
    # needlessly wide -- within limits, as five origins are a small sample.
    scale = float(min(max(np.percentile(norm, 80), 0.6), 2.5))
    return Backtest(float(np.mean(errs)), float(np.mean(pcts)), float(np.mean(inside)), used, h,
                    scale)


@dataclass
class Forecast:
    p10: np.ndarray
    p50: np.ndarray
    p90: np.ndarray
    paths: np.ndarray
    fit: Fit
    backtest: Backtest
    n: int
    confidence: str
    confidence_reason: str
    notes: list[str] = field(default_factory=list)


def confidence(n: int, bt: Backtest, *, unit: str, points_per_year: int) -> tuple[str, str]:
    """high | medium | low, and why, from history length and backtest error."""
    if n < 30:
        return "low", f"only {n} points of history"
    err = bt.mape if unit == "amount" else bt.mae
    if err is None:
        return "low", "too little history to test the forecast"
    limit = 0.02 if unit == "amount" else 0.25              # 2% / 25 bp
    good = err < limit
    if err > 3 * limit:
        return "low", f"the series is volatile: past forecasts missed by {_err(err, unit)}"
    if n >= points_per_year and good:
        return "high", f"{n} points of history; backtest error {_err(err, unit)}"
    if n >= points_per_year // 4 or good:
        return "medium", f"{n} points of history; backtest error {_err(err, unit)}"
    return "low", f"{n} points of history; backtest error {_err(err, unit)}"


def _err(err: float, unit: str) -> str:
    return f"{err * 100:.1f}%" if unit == "amount" else f"{err * 100:.0f} bp"


def forecast(values, steps: int, *, unit: str = "amount", points_per_year: int = 250,
             lo: float | None = None, hi: float | None = None,
             max_history: int = 750, log: bool | None = None,
             calibration: float = 1.0) -> Forecast:
    """Forecast `steps` points on from `values` (oldest first).

    `unit`: "amount" (errors in %) or "rate" (errors in percentage points).
    Amounts that are always positive (prices, balances) are modelled in logs:
    their moves are proportional, and a band can never go below zero.
    Only the most recent `max_history` points are used: older history says
    little about next month, and the cost stays flat as history grows.
    `calibration` widens or narrows the bands from the kept forecasts' record
    (`insights.learning.calibration`)."""
    y = np.asarray(values, dtype=float)[-max_history:]
    if len(y) == 0:
        raise ValueError("no history to forecast from")
    if log is None:
        log = unit == "amount" and bool(np.all(y > 0))
    z = np.log(y) if log else y
    f = fit(z, steps)
    bt = backtest(z, steps)
    # Bands as wide as the backtest showed they need to be: a model's own
    # one-step errors understate how far a month-ahead forecast can miss.
    sims = simulate(f, steps, lo=np.log(lo) if (log and lo) else lo,
                    hi=np.log(hi) if (log and hi) else hi, scale=bt.band_scale * calibration)
    if log:
        sims = np.exp(sims)
        # In logs the absolute error is a proportional one.
        bt = Backtest(None if bt.mae is None else float(np.exp(bt.mae) - 1),
                      None if bt.mae is None else float(np.exp(bt.mae) - 1),
                      bt.coverage, bt.origins, bt.horizon, bt.band_scale)
    p10, p50, p90 = np.percentile(sims, [10, 50, 90], axis=0)
    conf, why = confidence(len(y), bt, unit=unit, points_per_year=points_per_year)
    notes = []
    if abs(calibration - 1) > 0.02:
        notes.append(f"Bands {'widened' if calibration > 1 else 'narrowed'} {calibration:.2f}x "
                     f"from the record of past forecasts.")
    if bt.band_scale > 1.05:
        notes.append(f"Bands widened {bt.band_scale:.1f}x so that they would have held 80% of "
                     f"past outcomes.")
    elif bt.band_scale < 0.95:
        notes.append(f"Bands narrowed to {bt.band_scale:.1f}x: past outcomes fell well inside "
                     f"them.")
    if f.method != "damped trend":
        notes.append("Too little history to fit a trend: the forecast holds the last value.")
    return Forecast(p10, p50, p90, sims, f, bt, len(y), conf, why, notes)


# --- the calendar ------------------------------------------------------------------ #

#: Bangladesh's weekend: Friday and Saturday. Banks report those days with
#: Thursday's balances.
WEEKEND = frozenset({4, 5})


def business_days_after(start: date, until: date, weekend=WEEKEND) -> list[date]:
    out, d = [], start + timedelta(days=1)
    while d <= until:
        if d.weekday() not in weekend:
            out.append(d)
        d += timedelta(days=1)
    return out


def calendar_fill(days: list[date], values: np.ndarray, start: date, until: date) -> tuple[list[date], np.ndarray]:
    """Business-day values spread over every calendar day, each weekend day
    taking the business day before it -- how the bank's own data reads.

    `values` may be one series (n,) or paths (p, n)."""
    vals = np.atleast_2d(values)
    by_day = {d: i for i, d in enumerate(days)}
    cal, cols = [], []
    last = None
    d = start + timedelta(days=1)
    while d <= until:
        if d in by_day:
            last = by_day[d]
        if last is not None:
            cal.append(d)
            cols.append(last)
        d += timedelta(days=1)
    out = vals[:, cols] if cols else vals[:, :0]
    return cal, (out[0] if np.ndim(values) == 1 else out)


def month_end(d: date) -> date:
    nxt = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return nxt - timedelta(days=1)


def quarter_end(d: date) -> date:
    q_last_month = ((d.month - 1) // 3 + 1) * 3
    return month_end(date(d.year, q_last_month, 1))

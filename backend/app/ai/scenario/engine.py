"""What if rates move? The bank's NII, the branches' FTP profit and treasury's
result, under a scenario, from the aggregates alone.

The book is read as lines -- one per branch and product -- each with its
average daily balance and its balance-weighted rates over a base window.
Because the FTP rate is linear in its components
(`domain.calculation.ftp_rate`), a scenario recomputes each line exactly, with
no account-level data and no recalculation of facts: a slider can move and the
answer follows at once.

Three results, because an FTP move and a market move are different things:

- **Branches' FTP profit**: what the business units earn on the FTP spread.
  Moving a benchmark moves this, and treasury's result the opposite way.
- **Bank NII**: interest from customers minus interest to customers, plus what
  the surplus of deposits over loans earns when treasury invests it at the
  market rate. Only customer rates, balances and the market move this.
- **Treasury's result**: the difference -- the funding centre's share.

Repricing is gradual: a term deposit or a loan reprices when it rolls over, so
by a horizon of H days a product of tenor T has repriced H/T of its book (all
of it once H >= T). Demand deposits reprice when the bank decides.

Depositors respond to price: when our rate falls behind what other banks pay,
term deposits leave at `elasticity` percent per 100 bp of the shortfall
(demand deposits at half that), and come in at half that rate when we pay
more. An assumption, shown and editable, not a law.

PURE: no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import Decimal

_D0 = Decimal(0)
_MONTH = Decimal(30)


@dataclass(frozen=True)
class Line:
    """One branch x product: an average day of the base window."""
    branch: str
    product: str
    side: str                       # ASSET | LIABILITY
    nature: str | None              # DEMAND | TIME (deposits)
    tenor_days: int
    balance: Decimal                # average daily balance, taka
    roi: Decimal                    # customer rate, % a year
    benchmark: Decimal
    liquidity: Decimal
    other: Decimal
    divisor: Decimal = Decimal(36500)   # day-count divisor x 100

    def ftp_rate(self, benchmark: Decimal | None = None, roi: Decimal | None = None) -> Decimal:
        b = self.benchmark if benchmark is None else benchmark
        r = self.roi if roi is None else roi
        if self.side == "LIABILITY":
            return b - r - self.liquidity - self.other
        return r - b - self.liquidity - self.other


@dataclass(frozen=True)
class Scenario:
    """Every number a person can move. Rates in basis points, shares 0..1."""
    #: A parallel move in market rates: the policy rate, the curve.
    market_bp: int = 0
    #: How much of the market move FTP benchmarks follow -- market-priced
    #: products, and non-maturity deposits (priced by policy).
    bench_follow: float = 1.0
    bench_follow_demand: float = 0.0
    #: How much of the market move the bank passes to its customers.
    deposit_pass: float = 0.5
    demand_pass: float = 0.2
    loan_pass: float = 0.7
    #: Other banks' deposit rates move by this much beyond the market move.
    competitor_bp: int = 0
    #: Direct changes, product by product, in bp: the FTP benchmark, and the
    #: customer rate.
    product_bench_bp: dict[str, int] = field(default_factory=dict)
    product_rate_bp: dict[str, int] = field(default_factory=dict)
    #: Balance growth over the horizon, percent.
    deposit_growth_pct: float = 0.0
    loan_growth_pct: float = 0.0
    #: Percent of term deposits lost per 100 bp we fall behind other banks.
    elasticity: float = 2.0
    #: Apply the scenario to these branches only (empty: all).
    branches: tuple[str, ...] = ()
    horizon_months: int = 3

    @property
    def horizon_days(self) -> int:
        return self.horizon_months * 30

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["branches"] = list(self.branches)
        return d


#: How far each setting may go. Beyond these a scenario is not a scenario.
LIMITS = {"market_bp": (-500, 500), "competitor_bp": (-300, 300), "bench_bp": (-500, 500),
          "rate_bp": (-500, 500), "growth_pct": (-50.0, 50.0), "elasticity": (0.0, 20.0),
          "share": (0.0, 1.5), "horizon_months": (1, 24)}
MAX_RUNOFF = Decimal("0.30")


@dataclass
class LineResult:
    line: Line
    balance: Decimal
    roi: Decimal
    benchmark: Decimal
    ftp_base: Decimal               # per day, taka
    ftp_new: Decimal
    nii_base: Decimal               # customer interest per day: + loans, - deposits
    nii_new: Decimal
    repriced: Decimal               # share of the book repriced by the horizon


def _q(x: Decimal, places: str = "0.01") -> Decimal:
    return x.quantize(Decimal(places))


def apply(line: Line, s: Scenario, days: int | None = None) -> LineResult:
    """One line under the scenario, `days` into it (default: the horizon)."""
    days = s.horizon_days if days is None else days
    dm = Decimal(s.market_bp) / 100
    active = not s.branches or line.branch in s.branches
    demand = line.side == "LIABILITY" and (line.nature or "").upper() == "DEMAND"
    repriced = Decimal(1) if demand else min(Decimal(1), Decimal(days) / Decimal(max(line.tenor_days, 1)))
    if not active:
        dm_line, extra_b, extra_r = _D0, _D0, _D0
    else:
        dm_line = dm
        extra_b = Decimal(s.product_bench_bp.get(line.product, 0)) / 100
        extra_r = Decimal(s.product_rate_bp.get(line.product, 0)) / 100
    follow = Decimal(str(s.bench_follow_demand if demand else s.bench_follow))
    new_bench = line.benchmark + follow * dm_line + extra_b
    if line.side == "LIABILITY":
        pass_ = Decimal(str(s.demand_pass if demand else s.deposit_pass))
    else:
        pass_ = Decimal(str(s.loan_pass))
    d_roi = (pass_ * dm_line + extra_r) * repriced
    new_roi = line.roi + d_roi

    growth = Decimal(str(s.deposit_growth_pct if line.side == "LIABILITY" else s.loan_growth_pct)) / 100
    flow = _D0
    if line.side == "LIABILITY" and active:
        # Other banks pass the market move on as we assume we would, plus any
        # move of their own; depositors compare.
        theirs = Decimal(str(s.deposit_pass)) * dm + Decimal(s.competitor_bp) / 100
        behind = d_roi - theirs * (Decimal(1) if demand else repriced)
        e = Decimal(str(s.elasticity)) / 100 * (Decimal("0.5") if demand else Decimal(1))
        flow = e * behind if behind < 0 else e * behind / 2
        flow = max(-MAX_RUNOFF, min(MAX_RUNOFF, flow))
    ramp = min(Decimal(1), Decimal(days) / Decimal(max(s.horizon_days, 1)))
    new_bal = line.balance * (1 + (growth if active else _D0) * ramp + flow)

    div = line.divisor
    ftp_base = line.balance * line.ftp_rate() / div
    ftp_new = new_bal * line.ftp_rate(new_bench, new_roi) / div
    sign = 1 if line.side == "ASSET" else -1
    nii_base = sign * line.balance * line.roi / div
    nii_new = sign * new_bal * new_roi / div
    return LineResult(line, new_bal, new_roi, new_bench, ftp_base, ftp_new, nii_base, nii_new,
                      repriced)


@dataclass
class Totals:
    deposits: Decimal
    advances: Decimal
    branch_ftp: Decimal             # a month
    customer_nii: Decimal           # a month
    surplus_income: Decimal         # a month: the surplus invested at the market rate
    bank_nii: Decimal
    treasury: Decimal
    nim: Decimal | None             # % a year, on advances

    def to_dict(self) -> dict:
        return {k: (_q(v, "0.0001") if k == "nim" and v is not None else _q(v) if v is not None
                    else None) for k, v in self.__dict__.items()}


def _totals(rows: list[LineResult], *, new: bool, market_rate: Decimal, dm: Decimal) -> Totals:
    bal = (lambda r: r.balance) if new else (lambda r: r.line.balance)
    dep = sum((bal(r) for r in rows if r.line.side == "LIABILITY"), _D0)
    adv = sum((bal(r) for r in rows if r.line.side == "ASSET"), _D0)
    ftp = sum(((r.ftp_new if new else r.ftp_base) for r in rows), _D0) * _MONTH
    cust = sum(((r.nii_new if new else r.nii_base) for r in rows), _D0) * _MONTH
    rate = market_rate + (dm if new else _D0)
    surplus = (dep - adv) * rate / Decimal(36500) * _MONTH
    nii = cust + surplus
    nim = (nii / _MONTH * Decimal(36500) / adv) if adv else None
    return Totals(dep, adv, ftp, cust, surplus, nii, nii - ftp, nim)


def run(lines: list[Line], s: Scenario, *, market_rate: Decimal, days: int | None = None) -> dict:
    """The scenario against the base, in monthly taka, with its breakdowns.

    `market_rate`: what treasury earns on surplus funds today (% a year)."""
    rows = [apply(ln, s, days) for ln in lines]
    dm = Decimal(s.market_bp) / 100
    base = _totals(rows, new=False, market_rate=market_rate, dm=dm)
    new = _totals(rows, new=True, market_rate=market_rate, dm=dm)

    # Where the change in bank NII comes from. Rate and volume effects per
    # line sum exactly, with the interaction folded into volume.
    loan_rate = dep_rate = loan_vol = dep_vol = _D0
    for r in rows:
        ln = r.line
        rate_eff = (r.roi - ln.roi) * ln.balance / ln.divisor * _MONTH
        vol_eff = (r.balance - ln.balance) * r.roi / ln.divisor * _MONTH
        if ln.side == "ASSET":
            loan_rate += rate_eff
            loan_vol += vol_eff
        else:
            dep_rate -= rate_eff
            dep_vol -= vol_eff
    surplus_eff = new.surplus_income - base.surplus_income
    waterfall = [
        {"key": "loan_rates", "label": "Loan rates reprice", "value": _q(loan_rate)},
        {"key": "deposit_rates", "label": "Deposit rates reprice", "value": _q(dep_rate)},
        {"key": "loan_volume", "label": "Loan balances", "value": _q(loan_vol)},
        {"key": "deposit_volume", "label": "Deposit balances", "value": _q(dep_vol)},
        {"key": "surplus", "label": "Surplus funds at market rates", "value": _q(surplus_eff)},
    ]

    def group(key) -> list[dict]:
        acc: dict[str, dict] = {}
        for r in rows:
            k = key(r.line)
            a = acc.setdefault(k, {"key": k, "ftp_base": _D0, "ftp_new": _D0, "nii_base": _D0,
                                   "nii_new": _D0, "balance_base": _D0, "balance_new": _D0,
                                   "side": r.line.side})
            a["ftp_base"] += r.ftp_base * _MONTH
            a["ftp_new"] += r.ftp_new * _MONTH
            a["nii_base"] += r.nii_base * _MONTH
            a["nii_new"] += r.nii_new * _MONTH
            a["balance_base"] += r.line.balance
            a["balance_new"] += r.balance
        out = []
        for a in acc.values():
            out.append({**{k: (_q(v) if isinstance(v, Decimal) else v) for k, v in a.items()},
                        "ftp_change": _q(a["ftp_new"] - a["ftp_base"]),
                        "nii_change": _q(a["nii_new"] - a["nii_base"])})
        return sorted(out, key=lambda x: x["ftp_change"])

    return {
        "scenario": s.to_dict(), "days": s.horizon_days if days is None else days,
        "market_rate": market_rate,
        "base": base.to_dict(), "scenario_totals": new.to_dict(),
        "change": {k: (_q(getattr(new, k) - getattr(base, k), "0.0001") if k == "nim"
                       else _q(getattr(new, k) - getattr(base, k)))
                   if getattr(new, k) is not None and getattr(base, k) is not None else None
                   for k in base.__dict__},
        "waterfall": waterfall,
        "by_product": group(lambda ln: ln.product),
        "by_branch": group(lambda ln: ln.branch),
    }


def path(lines: list[Line], s: Scenario, *, market_rate: Decimal, step_days: int = 7) -> list[dict]:
    """The change in branches' FTP profit and bank NII per day, as the
    scenario phases in: one point per `step_days` up to the horizon."""
    out = []
    for d in range(step_days, s.horizon_days + step_days, step_days):
        d = min(d, s.horizon_days)
        rows = [apply(ln, s, d) for ln in lines]
        dm = Decimal(s.market_bp) / 100
        b = _totals(rows, new=False, market_rate=market_rate, dm=dm)
        n = _totals(rows, new=True, market_rate=market_rate, dm=dm)
        out.append({"day": d, "branch_ftp_per_day": _q((n.branch_ftp - b.branch_ftp) / _MONTH),
                    "bank_nii_per_day": _q((n.bank_nii - b.bank_nii) / _MONTH)})
        if d == s.horizon_days:
            break
    return out


# --- reading a scenario from JSON (a person's sliders or a model's reading) ------ #

class ScenarioError(ValueError):
    pass


def _num(v, lo, hi, kind=float):
    try:
        x = kind(v)
    except (TypeError, ValueError) as exc:
        raise ScenarioError(f"not a number: {v!r}") from exc
    if not lo <= x <= hi:
        raise ScenarioError(f"{x} is outside {lo}..{hi}")
    return x


def parse(d: dict, *, products: set[str] | None = None, branches: set[str] | None = None) -> Scenario:
    """A scenario from a dict, every field checked; unknown fields ignored."""
    if not isinstance(d, dict):
        raise ScenarioError("a scenario is an object")
    s = Scenario()
    kw: dict = {}
    for k in ("market_bp", "competitor_bp"):
        if d.get(k) is not None:
            kw[k] = _num(d[k], *LIMITS[k], kind=lambda v: int(round(float(v))))
    for k in ("bench_follow", "bench_follow_demand", "deposit_pass", "demand_pass", "loan_pass"):
        if d.get(k) is not None:
            kw[k] = _num(d[k], *LIMITS["share"])
    for k in ("deposit_growth_pct", "loan_growth_pct"):
        if d.get(k) is not None:
            kw[k] = _num(d[k], *LIMITS["growth_pct"])
    if d.get("elasticity") is not None:
        kw["elasticity"] = _num(d["elasticity"], *LIMITS["elasticity"])
    if d.get("horizon_months") is not None:
        kw["horizon_months"] = _num(d["horizon_months"], *LIMITS["horizon_months"],
                                    kind=lambda v: int(round(float(v))))
    for k, lim in (("product_bench_bp", "bench_bp"), ("product_rate_bp", "rate_bp")):
        m = d.get(k) or {}
        if not isinstance(m, dict):
            raise ScenarioError(f"{k} must map product codes to basis points")
        out = {}
        for code, v in list(m.items())[:40]:
            if products is not None and code not in products:
                raise ScenarioError(f"unknown product {code!r}")
            out[str(code)] = _num(v, *LIMITS[lim], kind=lambda x: int(round(float(x))))
        kw[k] = out
    br = d.get("branches") or []
    if not isinstance(br, list):
        raise ScenarioError("branches must be a list")
    if branches is not None:
        bad = [b for b in br if b not in branches]
        if bad:
            raise ScenarioError(f"unknown or out-of-scope branch {bad[0]!r}")
    kw["branches"] = tuple(str(b) for b in br[:200])
    return replace(s, **kw)

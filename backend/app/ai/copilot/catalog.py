"""What Ask FTP can do: metrics, periods, dimensions and tools, and the plan.

A question becomes a *plan* -- a small JSON object naming one read-only tool
and its arguments -- written by the model from the masked question alone. The
model never sees data while planning and never writes SQL: it picks from this
catalogue, `parse_plan` checks every field against it, and the server runs
the plan in the asker's own scope. What the model cannot express here, the
copilot cannot do.

PURE: no I/O.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation


@dataclass(frozen=True)
class Metric:
    label: str
    unit: str            # bdt | pct
    #: flow: summed over the period; stock: average daily balance; rate: annualised %.
    kind: str
    help: str


METRICS: dict[str, Metric] = {
    "net_ftp_profit": Metric("Net FTP profit", "bdt", "flow",
                             "FTP profit of loans and deposits together over the period"),
    "lending_ftp_profit": Metric("Lending FTP profit", "bdt", "flow", "FTP profit from loans"),
    "deposit_ftp_profit": Metric("Deposit FTP profit", "bdt", "flow", "FTP profit from deposits"),
    "deposits": Metric("Deposits", "bdt", "stock", "average daily deposit balance"),
    "advances": Metric("Advances", "bdt", "stock", "average daily loan balance"),
    "cost_of_deposits": Metric("Cost of deposits", "pct", "rate",
                               "interest paid on deposits, % a year"),
    "yield_on_advances": Metric("Yield on advances", "pct", "rate",
                                "interest earned on loans, % a year"),
    "nim": Metric("Net interest margin", "pct", "rate",
                  "interest earned minus paid, over loans, % a year"),
    "spread": Metric("Gross spread", "pct", "rate", "yield on advances minus cost of deposits"),
    "ftp_yield": Metric("FTP yield", "pct", "rate", "net FTP profit over all balances, % a year"),
    "accounts": Metric("Accounts", "count", "stock", "number of accounts, average per day"),
    "loss_making_accounts": Metric("Loss-making accounts", "count", "stock",
                                   "accounts with a negative FTP rate, average per day"),
}

PROFIT_METRICS = ("net_ftp_profit", "lending_ftp_profit", "deposit_ftp_profit")
DIMENSIONS = ("total", "branch", "product", "division", "district", "category")
PERIODS = ("latest_day", "last_7_days", "last_30_days", "this_month", "last_month", "all", "custom")
CHARTS = ("bar", "line", "none")
TOOLS = ("compare", "trend", "why", "market", "benchmarks", "insights", "explain", "clarify",
         "forecast", "market_forecast", "policy_outlook", "scenario", "peer_compare", "market_rates")
#: Tools that look ahead or outside the bank; run by `copilot.tools_intel`.
INTEL_TOOLS = ("forecast", "market_forecast", "policy_outlook", "scenario", "peer_compare",
               "market_rates")
PEER_SETS = ("competitors", "pcb", "fb", "scb", "islamic", "all")
FORECAST_METRICS = ("deposits", "advances", "net_ftp_profit", "cost_of_deposits",
                    "yield_on_advances", "nim")
FORECAST_CODES = ("BB_CALL_ON", "BB_TBILL_91", "BB_TBILL_364", "BB_TBOND_5Y", "FX_USDBDT",
                  "BD_IND_DEPOSIT", "BD_IND_ADVANCE", "US_FEDFUNDS", "US_UST_10Y", "BRENT")
OPS = ("gt", "lt", "change_gt", "change_lt")
SIDES = ("ASSET", "LIABILITY")
CATEGORIES = ("URBAN", "SEMI_URBAN", "RURAL")
MARKET_CODES = ("BB_POLICY", "BB_CALL_ON", "BB_DOMMR_1M", "BB_TBILL_91", "BB_TBILL_182",
                "BB_TBILL_364", "BB_TBOND_2Y", "BB_TBOND_5Y", "BB_TBOND_10Y", "FX_USDBDT",
                "US_FEDFUNDS", "US_UST_10Y", "BRENT")
MAX_LIMIT = 100


@dataclass(frozen=True)
class Condition:
    metric: str
    op: str
    #: Natural units: percentage points for a rate (25 bp = 0.25), taka for money.
    value: Decimal


@dataclass(frozen=True)
class Plan:
    tool: str
    metrics: tuple[str, ...] = ("net_ftp_profit",)
    by: str = "total"
    period: str = "last_7_days"
    date_from: date | None = None
    date_to: date | None = None
    compare: bool = False
    #: Filters as the model wrote them: vault tokens (BR_K7Q), product names.
    division: str | None = None
    district: str | None = None
    branches: tuple[str, ...] = ()
    products: tuple[str, ...] = ()
    side: str | None = None
    category: str | None = None
    where: tuple[Condition, ...] = ()
    sort: str | None = None            # a metric, or "change:<metric>"
    order: str = "desc"
    limit: int = 25
    chart: str = "bar"
    codes: tuple[str, ...] = ()
    title: str = ""
    #: For clarify: the question to put back to the person.
    message: str = ""
    #: The question (or plan) chose its own period; a page's dates do not apply.
    period_set: bool = False
    #: For scenario: the what-if's settings, checked when it runs.
    scenario: dict | None = None
    #: For market_rates: other banks and rate categories as the model wrote
    #: them (resolved when it runs), the comparison set, and whether to add
    #: our own book's rates.
    peer_banks: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    peer_set: str | None = None
    include_ours: bool = True

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items()}
        d["date_from"] = self.date_from.isoformat() if self.date_from else None
        d["date_to"] = self.date_to.isoformat() if self.date_to else None
        d["where"] = [{"metric": c.metric, "op": c.op, "value": str(c.value)} for c in self.where]
        for k in ("metrics", "branches", "products", "codes", "peer_banks", "categories"):
            d[k] = list(d[k])
        return d


class PlanError(ValueError):
    pass


def _str(v, max_len: int = 120) -> str | None:
    if v is None or v == "":
        return None
    if not isinstance(v, (str, int)):
        raise PlanError(f"expected text, got {type(v).__name__}")
    return str(v).strip()[:max_len] or None


def _list(v, max_len: int = 20) -> tuple[str, ...]:
    if v is None:
        return ()
    if isinstance(v, (str, int)):
        v = [v]
    if not isinstance(v, list):
        raise PlanError("expected a list")
    return tuple(s for x in v[:max_len] if (s := _str(x)))


def _date(v) -> date | None:
    s = _str(v, 10)
    if s is None:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError as exc:
        raise PlanError(f"bad date {s!r}") from exc


def _dec(v) -> Decimal:
    try:
        return Decimal(str(v).replace(",", "").strip())
    except (InvalidOperation, AttributeError) as exc:
        raise PlanError(f"bad number {v!r}") from exc


def parse_plan(raw: str | dict) -> Plan:
    """A plan from the model's JSON, checked field by field against the catalogue."""
    if isinstance(raw, str):
        text = raw.strip()
        # Some models wrap JSON in a code fence despite being asked not to.
        if text.startswith("```"):
            text = text.strip("`").removeprefix("json").strip()
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end < start:
            raise PlanError("no JSON object in the reply")
        try:
            raw = json.loads(text[start:end + 1])
        except json.JSONDecodeError as exc:
            raise PlanError(f"the reply is not valid JSON: {exc.msg}") from exc
    if not isinstance(raw, dict):
        raise PlanError("the plan is not an object")
    d = raw
    tool = _str(d.get("tool"), 20)
    if tool not in TOOLS:
        raise PlanError(f"unknown tool {tool!r}")
    if tool in ("clarify", "explain"):
        return Plan(tool=tool, message=_str(d.get("message"), 400) or "", title=_str(d.get("title")) or "")
    if tool in INTEL_TOOLS:
        return _intel_plan(tool, d)

    metrics = tuple(m for m in _list(d.get("metrics")) if m in METRICS) or ("net_ftp_profit",)
    by = _str(d.get("by"), 20) or ("total" if tool != "why" else "product")
    if by not in DIMENSIONS:
        raise PlanError(f"unknown dimension {by!r}")
    if tool == "why" and by == "total":
        by = "product"
    period = _str(d.get("period"), 20) or "last_7_days"
    if period not in PERIODS:
        raise PlanError(f"unknown period {period!r}")
    date_from, date_to = _date(d.get("date_from")), _date(d.get("date_to"))
    if period == "custom":
        if not date_from or not date_to or date_from > date_to:
            raise PlanError("a custom period needs date_from <= date_to")
    side = _str(d.get("side"), 10)
    side = side.upper() if side else None
    if side is not None and side not in SIDES:
        raise PlanError(f"unknown side {side!r}")
    cat = _str(d.get("category"), 20)
    cat = cat.upper().replace(" ", "_").replace("-", "_") if cat else None
    if cat is not None and cat not in CATEGORIES:
        raise PlanError(f"unknown category {cat!r}")

    where = []
    for c in (d.get("where") or [])[:4]:
        if not isinstance(c, dict):
            raise PlanError("a condition must be an object")
        m, op = _str(c.get("metric"), 40), _str(c.get("op"), 12)
        if m not in METRICS or op not in OPS:
            raise PlanError(f"bad condition {c!r}")
        value = _dec(c["value_bp"]) / 100 if c.get("value_bp") is not None else _dec(c.get("value"))
        where.append(Condition(m, op, value))
        if m not in metrics:
            metrics = metrics + (m,)
    # "above X and below Y" on the same measure with X >= Y can match nothing:
    # a model's muddle, not a filter. Drop the pair rather than return nothing.
    for m in {c.metric for c in where}:
        for kind in ("", "change_"):
            gt = [c for c in where if c.metric == m and c.op == f"{kind}gt"]
            lt = [c for c in where if c.metric == m and c.op == f"{kind}lt"]
            if gt and lt and max(c.value for c in gt) >= min(c.value for c in lt):
                where = [c for c in where if c not in gt + lt]
    # "why" explains FTP profit. A question about another measure (deposits,
    # cost of deposits...) is a comparison of that measure, period on period.
    if tool == "why" and d.get("metrics") and not any(
            m in PROFIT_METRICS for m in _list(d.get("metrics"))):
        tool, was_why = "compare", True
    else:
        was_why = False
    if _list(d.get("branches")) and d.get("by") in (None, "", "total"):
        by = "branch"
    compare = bool(d.get("compare")) or any(c.op.startswith("change") for c in where) \
        or tool == "why" or was_why

    sort = _str(d.get("sort"), 50)
    if sort is not None:
        base = sort.removeprefix("change:")
        if base not in METRICS:
            sort = None
        elif sort.startswith("change:"):
            compare = True
    order = "asc" if _str(d.get("order"), 5) == "asc" else "desc"
    try:
        limit = max(1, min(int(d.get("limit") or 25), MAX_LIMIT))
    except (TypeError, ValueError):
        limit = 25
    chart = _str(d.get("chart"), 5) or ("line" if tool == "trend" else "bar")
    if chart not in CHARTS:
        chart = "bar"
    codes = tuple(c.upper() for c in _list(d.get("codes")) if c.upper() in MARKET_CODES)

    return Plan(tool=tool, metrics=metrics[:4], by=by, period=period, date_from=date_from,
                date_to=date_to, compare=compare, division=_str(d.get("division")),
                district=_str(d.get("district")), branches=_list(d.get("branches")),
                products=_list(d.get("products")), side=side, category=cat, where=tuple(where),
                sort=sort, order=order, limit=limit, chart=chart, codes=codes,
                title=_str(d.get("title"), 90) or "",
                period_set=bool(d.get("period") or d.get("date_from") or d.get("date_to")))


def _intel_plan(tool: str, d: dict) -> Plan:
    title = _str(d.get("title"), 90) or ""
    if tool == "forecast":
        metrics = tuple(m for m in _list(d.get("metrics")) if m in FORECAST_METRICS) \
            or ("net_ftp_profit", "deposits")
        return Plan(tool=tool, metrics=metrics[:4], branches=_list(d.get("branches"))[:1],
                    title=title, chart="line")
    if tool == "market_forecast":
        codes = tuple(c.upper() for c in _list(d.get("codes")) if c.upper() in FORECAST_CODES) \
            or ("BB_CALL_ON",)
        return Plan(tool=tool, codes=codes[:6], title=title, chart="line")
    if tool == "scenario":
        sc = d.get("scenario")
        if sc is None:
            sc = {k: v for k, v in d.items() if k not in ("tool", "title", "thought")}
        if not isinstance(sc, dict):
            raise PlanError("a scenario must be an object of settings")
        return Plan(tool=tool, scenario=sc, title=title, chart="bar")
    side = _str(d.get("side"), 10)
    side = side.upper() if side else None
    if side is not None and side not in SIDES:
        raise PlanError(f"unknown side {side!r}")
    if tool == "market_rates":
        peer_set = _str(d.get("group") or d.get("peer_set"), 20)
        if peer_set is not None and peer_set not in PEER_SETS:
            raise PlanError(f"unknown group {peer_set!r}")
        try:
            limit = max(1, min(int(d.get("limit") or 15), 61))
        except (TypeError, ValueError):
            limit = 15
        return Plan(tool=tool, side=side, title=title, chart="bar",
                    peer_banks=_list(d.get("banks"), 8), categories=_list(d.get("products")
                                                                         or d.get("categories"), 6),
                    peer_set=peer_set, limit=limit,
                    # Unsaid, the order is the customer's: deposits highest
                    # first, loans cheapest first (decided when it runs).
                    order={"asc": "asc", "desc": "desc"}.get(_str(d.get("order"), 5) or "", "auto"),
                    include_ours=d.get("include_ours") is not False)
    return Plan(tool=tool, side=side, title=title, chart="none" if tool == "policy_outlook" else "bar")


def window(p: Plan, latest: date, earliest: date) -> tuple[date, date]:
    """The plan's period as dates, relative to the latest business date."""
    if p.period == "latest_day":
        return latest, latest
    if p.period == "last_7_days":
        return max(earliest, latest - timedelta(days=6)), latest
    if p.period == "last_30_days":
        return max(earliest, latest - timedelta(days=29)), latest
    if p.period == "this_month":
        return max(earliest, latest.replace(day=1)), latest
    if p.period == "last_month":
        end = latest.replace(day=1) - timedelta(days=1)
        return max(earliest, end.replace(day=1)), end
    if p.period == "custom" and p.date_from and p.date_to:
        return max(earliest, p.date_from), min(latest, p.date_to)
    return earliest, latest


def prior(start: date, end: date) -> tuple[date, date]:
    """The window of the same length immediately before."""
    n = (end - start).days + 1
    return start - timedelta(days=n), start - timedelta(days=1)


#: What each page shows, so a question asked on it can be read in its light.
PAGES: dict[str, tuple[str, str]] = {
    "basic": ("Basic overview", "balances, interest and FTP profit by branch, and the daily FTP profit trend"),
    "daily": ("Daily", "what needs attention, the banking ratios (yield on advances, cost of deposits, "
                       "spread, NIM, CASA, credit-deposit), and yield and cost by product"),
    "overview": ("Overview", "net FTP profit with its change on the previous period, trend, and sources of margin"),
    "analytics": ("Analytics", "why FTP profit changed (volume and rate), concentration, and rate distribution"),
    "leaders": ("Leaders", "the best branches in each group and which product leads where"),
    "accounts": ("Accounts", "loss-making accounts and repricing opportunities"),
    "consolidated": ("Consolidated", "every account-day as one row"),
    "intel": ("Intelligence", "the morning brief, open findings, the market curve and FTP benchmarks against it"),
    "coach": ("Branch coach", "one branch's rank, its gap to its peers' median, and its actions for the week"),
    "outlook": ("Outlook", "forecasts of the book to month-end, market rates 90 days ahead, which way the "
                           "policy rate leans at the next MPC, and our product rates against other banks'"),
    "pulse": ("Bank pulse", "the bank's health score and its parts, the bank against the industry, "
                            "the month's forecast, and the top risks and openings"),
    "scenario": ("Scenario lab", "a what-if on rates, pass-through and balances, and its effect on "
                                 "bank NII, branch FTP profit and treasury"),
    "market": ("Market rates", "every bank's posted deposit and lending rates (Bangladesh Bank's "
                               "bank-wise tables), our posted rates and our customers' actual rates"),
    "rates": ("Rate configuration", "the FTP benchmark, liquidity and other cost rates in force"),
    "upload": ("Upload", "loading bank data files and their checks"),
    "admin": ("Master data", "the branch and product masters"),
    "activity": ("Activity log", "changes made in the system"),
    "ai": ("AI management", "AI providers and the data policy"),
}

_PAGE_SUGGESTIONS = {
    "daily": ["Why did cost of deposits move this week?", "Which products have the lowest FTP yield?"],
    "basic": ["Which 5 branches made the most FTP profit?", "Why did FTP profit change this week?"],
    "overview": ["Why did net FTP profit change?", "How has FTP profit moved day by day?"],
    "analytics": ["Which products drove the change in FTP profit?", "Which branches' FTP yield fell most?"],
    "leaders": ["Rank the branches by FTP yield", "Which divisions made the most FTP profit?"],
    "accounts": ["Which products have the most loss-making accounts?"],
    "rates": ["Which benchmarks are furthest from the market?"],
    "intel": ["Which of these findings is worth most?", "Which benchmarks are furthest from the market?",
              "Where are call money and T-bill yields today?"],
    "outlook": ["How sure is this month's profit forecast?",
                "What would a 50 bp cut do to this?",
                "Why do the signals lean this way?"],
    "pulse": ["Why is our weakest part low, and what would fix it?",
              "Which branches pull the score down?",
              "How do we compare with the industry on cost of deposits?"],
    "scenario": ["Explain this result in plain words",
                 "And if we passed only 30% to term depositors?",
                 "Which branches are hit hardest under this?"],
    "market": ["Which banks pay the most on a 1-year FD?",
               "How does City Bank price against us?",
               "Which private banks are cheapest for home loans?"],
    "coach": ["Why is this branch's cost of deposits above its peers?",
              "How has this branch's FTP profit moved over the last 30 days?"],
}


def suggestions(scope: str, page: str | None = None) -> list[str]:
    """Questions worth asking first, for the asker's level and the page they are on."""
    first = _PAGE_SUGGESTIONS.get(page or "", [])
    return first + [q for q in _suggestions(scope) if q not in first]


def _suggestions(scope: str) -> list[str]:
    common = ["Which 5 branches made the most FTP profit last week?",
              "Why did FTP profit change this week?",
              "Which products have the lowest FTP yield this month?",
              "How has net interest margin moved over the last 30 days?",
              "Where are call money and T-bill yields today?"]
    if scope == "HO":
        return ["Which benchmarks are furthest from the market?",
                "Which divisions' cost of deposits rose this month?"] + common
    if scope == "DIVISION":
        return ["Branches in my division where cost of deposits rose this month",
                "Rank my branches by FTP yield last week"] + common
    return ["How did my FTP profit change week on week?",
            "What is my cost of deposits and how has it moved?"] + common[3:]


# --- the planner's instructions ---------------------------------------------- #

def planner_system() -> str:
    metrics = "\n".join(f"  {k}: {m.label} ({m.help})" for k, m in METRICS.items())
    return f"""You turn a banker's question into ONE query plan for the FTP (funds transfer pricing) platform of a Bangladeshi bank. You never see data; you only choose what to run.

Reply with a single JSON object, no prose. Fields:
  tool: one of
    "compare"    metrics for the whole book or broken down by a dimension, optionally against the previous period of equal length
    "trend"      metrics day by day over the period
    "why"        why net FTP profit changed: split into volume (balances) and rate (spreads), by a dimension
    "market"     market rates and prices (codes below)
    "benchmarks" the bank's FTP benchmark rates against the market curve, product by product
    "insights"   the platform's current alerts and findings
    "forecast"   where the book is heading: month-end, quarter-end and 90 days ahead, with a likely range (metrics from: {", ".join(FORECAST_METRICS)}; one branch token optional)
    "market_forecast"  market rates 90 days ahead with a likely range (codes from: {", ".join(FORECAST_CODES)})
    "policy_outlook"   which way Bangladesh Bank's policy rate leans at the next MPC meeting, and why
    "scenario"   a what-if: put the settings in "scenario": {{"market_bp": move in bp, "deposit_pass": 0..1, "loan_pass": 0..1, "competitor_bp": other banks' deposit rates move, "deposit_growth_pct", "loan_growth_pct", "horizon_months", "product_rate_bp": {{"PRODUCT_CODE": bp}}}}; omit what the question does not set
    "peer_compare"  our product rates against other banks' posted rates (Bangladesh Bank's bank-wise tables); side optional
    "market_rates"  look up other banks' posted rates (Bangladesh Bank's monthly tables, 61 banks): "banks": names as written ("City Bank", "EBL", "BRAC"), "products": rate types in words ("1 year FD", "savings", "home loan", "SME working capital"), "group": "competitors"|"pcb"|"fb"|"scb"|"islamic"|"all", "order": "desc" (highest first) or "asc", "limit", "include_ours": true to add our own rates
    "explain"    a concept question needing no data ("what is FTP?"); put nothing else
    "clarify"    the question is ambiguous or impossible; put your question to the user in "message"
  metrics: list of up to 4 of
{metrics}
  by: "total" | "branch" | "product" | "division" | "district" | "category"
  period: "latest_day" | "last_7_days" | "last_30_days" | "this_month" | "last_month" | "all" | "custom" (then date_from, date_to as YYYY-MM-DD)
  compare: true to add the previous period of the same length and the change
  division, district: a token like DIV_4TA / DIST_X2N exactly as written in the question
  branches: list of branch tokens like BR_K7Q exactly as written in the question
  products: list of product names exactly as written, e.g. ["SAVINGS ACCOUNT - STANDARD"]
  side: "ASSET" (loans) | "LIABILITY" (deposits)
  category: "URBAN" | "SEMI_URBAN" | "RURAL"
  where: list of {{"metric": ..., "op": "gt"|"lt"|"change_gt"|"change_lt", "value": number}}; rates in percentage points (or "value_bp" in basis points), money in taka
  sort: a metric, or "change:<metric>"; order: "desc" | "asc"; limit: 1-100
  chart: "bar" | "line" | "none"
  codes (market only): any of {", ".join(MARKET_CODES)}
  title: a short title for the answer, in the user's language

Rules:
- Tokens (BR_…, DIST_…, DIV_…) stand for real names you are not shown. Copy them exactly; never invent one.
- When the question names branches, put every one of their tokens in "branches" and use by "branch".
- "why" is only for FTP profit. For why deposits, loans or a rate moved, use "compare" with compare: true.
- Do not assume the question's premise is true: plan the query that would show whether it is.
- "Rose/fell/changed/grew" needs compare: true, and "rose by more than X" is a change_gt condition.
- "Top/best/worst N" is sort + order + limit. "Loans" means side ASSET, "deposits" side LIABILITY.
- Periods are relative to the latest business date given below, not to today.
- Questions about the future ("will", "by month-end", "next quarter", "expect") use "forecast", "market_forecast" or "policy_outlook"; "what if" questions use "scenario".
- Prefer "compare" when unsure. Use "clarify" only when no sensible plan exists.

Examples:
Q: which 5 branches made the most FTP profit last week?
{{"tool":"compare","metrics":["net_ftp_profit"],"by":"branch","period":"last_7_days","sort":"net_ftp_profit","order":"desc","limit":5,"chart":"bar","title":"Top 5 branches by FTP profit"}}
Q: branches in DIV_4TA whose cost of deposits rose more than 25 bp this month
{{"tool":"compare","metrics":["cost_of_deposits"],"by":"branch","division":"DIV_4TA","period":"this_month","compare":true,"where":[{{"metric":"cost_of_deposits","op":"change_gt","value_bp":25}}],"sort":"change:cost_of_deposits","order":"desc","chart":"bar","title":"Branches where deposits got dearer"}}
Q: why did BR_K7Q lose deposits while BR_2MX grew?
{{"tool":"compare","metrics":["deposits","cost_of_deposits"],"by":"branch","branches":["BR_K7Q","BR_2MX"],"period":"last_7_days","compare":true,"chart":"bar","title":"Deposits at the two branches"}}
Q: how has NIM moved over the last month?
{{"tool":"trend","metrics":["nim"],"period":"last_30_days","chart":"line","title":"Net interest margin, daily"}}
Q: why did profit fall this week?
{{"tool":"why","by":"product","period":"last_7_days","chart":"bar","title":"What moved FTP profit"}}
Q: will we beat last month's profit?
{{"tool":"forecast","metrics":["net_ftp_profit"],"title":"This month's FTP profit, forecast"}}
Q: will Bangladesh Bank cut rates?
{{"tool":"policy_outlook","title":"The next MPC meeting"}}
Q: what happens to our NII if BB hikes 50 bp?
{{"tool":"scenario","scenario":{{"market_bp":50}},"title":"A 50 bp hike"}}
Q: are our term deposit rates competitive?
{{"tool":"peer_compare","side":"LIABILITY","title":"Our deposit rates against other banks"}}
Q: which banks pay the most on a 1-year FD?
{{"tool":"market_rates","products":["1 year FD"],"group":"all","order":"desc","limit":10,"title":"Highest 1-year FD rates"}}
Q: show City Bank's deposit rates against ours
{{"tool":"market_rates","banks":["City Bank"],"side":"LIABILITY","title":"City Bank against us"}}
Q: compare EBL, Prime and BRAC on home loans
{{"tool":"market_rates","banks":["EBL","Prime","BRAC"],"products":["home loan"],"title":"Home loan rates"}}
Q: where is the call money rate?
{{"tool":"market","codes":["BB_CALL_ON","BB_DOMMR_1M"],"chart":"none","title":"Call money"}}"""

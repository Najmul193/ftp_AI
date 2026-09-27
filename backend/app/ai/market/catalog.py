"""The market series the module knows about.

`tenor_days` places a rate on the curve; `None` for anything that is not a
point on the Bangladesh taka curve (FX, global rates, commodities).

PURE: no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Series:
    code: str
    name: str
    short: str
    category: str       # bd_policy | bd_money | bd_govt | fx | global_rates | commodity
    unit: str           # pct | bdt | usd | index
    source: str         # bb_paste | exchangerate_api | fred
    tenor_days: int | None = None
    #: The upstream id where the source has one (FRED series id, currency code).
    upstream: str | None = None
    #: How many days old before the value is called stale.
    stale_after_days: int = 5


SERIES: tuple[Series, ...] = (
    # --- Bangladesh Bank policy corridor (changes at monetary policy statements)
    Series("BB_POLICY", "Bangladesh Bank policy rate (repo)", "Policy rate", "bd_policy", "pct",
           "bb_paste", None, stale_after_days=400),
    Series("BB_SLF", "Standing lending facility", "SLF", "bd_policy", "pct", "bb_paste",
           None, stale_after_days=400),
    Series("BB_SDF", "Standing deposit facility", "SDF", "bd_policy", "pct", "bb_paste",
           None, stale_after_days=400),
    # --- Interbank money market
    Series("BB_CALL_ON", "Call money, overnight (weighted average)", "Call money O/N", "bd_money",
           "pct", "bb_paste", 1),
    Series("BB_CALL_ON_VOL", "Call money, overnight volume", "Call volume", "bd_money", "bdt_cr",
           "bb_paste", None),
    Series("BB_SN_7D", "Short notice, 7 days (weighted average)", "Short notice 7D", "bd_money",
           "pct", "bb_paste", 7),
    Series("BB_DOMMR_ON", "DOMMR overnight", "DOMMR O/N", "bd_money", "pct", "bb_paste", 1),
    Series("BB_DOMMR_1W", "DOMMR 1 week", "DOMMR 1W", "bd_money", "pct", "bb_paste", 7),
    Series("BB_DOMMR_1M", "DOMMR 1 month", "DOMMR 1M", "bd_money", "pct", "bb_paste", 30),
    Series("BB_DOMMR_3M", "DOMMR 3 months", "DOMMR 3M", "bd_money", "pct", "bb_paste", 90),
    Series("BB_BOFR_ON", "BOFR overnight", "BOFR O/N", "bd_money", "pct", "bb_paste", 1),
    Series("BB_BOFR_1W", "BOFR 1 week", "BOFR 1W", "bd_money", "pct", "bb_paste", 7),
    # --- Government securities: auction cut-off yields (weekly)
    Series("BB_TBILL_91", "91-day treasury bill, cut-off yield", "T-bill 91D", "bd_govt", "pct",
           "bb_paste", 91, stale_after_days=14),
    Series("BB_TBILL_182", "182-day treasury bill, cut-off yield", "T-bill 182D", "bd_govt", "pct",
           "bb_paste", 182, stale_after_days=21),
    Series("BB_TBILL_364", "364-day treasury bill, cut-off yield", "T-bill 364D", "bd_govt", "pct",
           "bb_paste", 364, stale_after_days=21),
    Series("BB_TBOND_2Y", "2-year treasury bond, cut-off yield", "T-bond 2Y", "bd_govt", "pct",
           "bb_paste", 730, stale_after_days=45),
    Series("BB_TBOND_5Y", "5-year treasury bond, cut-off yield", "T-bond 5Y", "bd_govt", "pct",
           "bb_paste", 1825, stale_after_days=45),
    Series("BB_TBOND_10Y", "10-year treasury bond, cut-off yield", "T-bond 10Y", "bd_govt", "pct",
           "bb_paste", 3650, stale_after_days=45),
    Series("BB_TBOND_15Y", "15-year treasury bond, cut-off yield", "T-bond 15Y", "bd_govt", "pct",
           "bb_paste", 5475, stale_after_days=60),
    Series("BB_TBOND_20Y", "20-year treasury bond, cut-off yield", "T-bond 20Y", "bd_govt", "pct",
           "bb_paste", 7300, stale_after_days=60),
    # --- Foreign exchange (market mid, not Bangladesh Bank's official rate)
    Series("FX_USDBDT", "US dollar in taka", "USD/BDT", "fx", "bdt", "exchangerate_api",
           upstream="USD", stale_after_days=3),
    Series("FX_EURBDT", "Euro in taka", "EUR/BDT", "fx", "bdt", "exchangerate_api",
           upstream="EUR", stale_after_days=3),
    Series("FX_GBPBDT", "Pound sterling in taka", "GBP/BDT", "fx", "bdt", "exchangerate_api",
           upstream="GBP", stale_after_days=3),
    # --- Global rates and commodities (FRED, St. Louis Fed)
    Series("US_FEDFUNDS", "US federal funds effective rate", "Fed funds", "global_rates", "pct",
           "fred", upstream="DFF"),
    Series("US_SOFR", "Secured overnight financing rate", "SOFR", "global_rates", "pct",
           "fred", upstream="SOFR"),
    Series("US_TBILL_3M", "US 3-month treasury bill", "UST 3M", "global_rates", "pct",
           "fred", upstream="DTB3"),
    Series("US_UST_2Y", "US 2-year treasury yield", "UST 2Y", "global_rates", "pct",
           "fred", upstream="DGS2"),
    Series("US_UST_10Y", "US 10-year treasury yield", "UST 10Y", "global_rates", "pct",
           "fred", upstream="DGS10"),
    Series("BRENT", "Brent crude oil", "Brent", "commodity", "usd", "fred",
           upstream="DCOILBRENTEU", stale_after_days=7),
    Series("USD_BROAD", "US dollar broad index", "USD index", "global_rates", "index", "fred",
           upstream="DTWEXBGS", stale_after_days=10),
)

BY_CODE: dict[str, Series] = {s.code: s for s in SERIES}

#: Series treasury enters (pasted or typed): the only ones the entry API accepts.
MANUAL = frozenset(s.code for s in SERIES if s.source == "bb_paste")

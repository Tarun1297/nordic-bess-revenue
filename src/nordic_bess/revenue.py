"""Battery revenue strategies as pure functions: prices in, EUR out, no I/O."""

import logging

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


def to_wide(prices: pd.DataFrame) -> pd.DataFrame:
    """Pivot the tidy price table to one column per product, indexed by UTC hour."""
    return prices.pivot(index="timestamp_utc", columns="product", values="price")


def fcr_n_only(wide: pd.DataFrame, bid_mw: float) -> pd.Series:
    """Hourly EUR from offering bid_mw of FCR-N every hour."""
    return (wide["fcr_n"] * bid_mw).rename("fcr_n")


def fcr_d_stacked(wide: pd.DataFrame, bid_mw: float) -> pd.Series:
    """Hourly EUR from offering bid_mw of FCR-D up and bid_mw of FCR-D down in the same hour.

    Stacking is allowed: Fingrid's battery report (April 2025, p. 7) says batteries can take part
    in both directions simultaneously, and the Technical Requirements (p. 40) sum the two
    products' power and energy reservations. Both directions can share one battery because
    frequency cannot be below 49.9 Hz and above 50.1 Hz at once.
    """
    return ((wide["fcr_d_up"] + wide["fcr_d_down"]) * bid_mw).rename("fcr_d_stacked")


def arbitrage(day_ahead: pd.Series, energy_mwh: float, efficiency: float, tz: str) -> pd.Series:
    """Hourly EUR from one perfect-foresight charge/discharge cycle per market day.

    For each day, pick the charge hour c and later discharge hour d that maximise
    p_d * E - p_c * E / efficiency; trade only if that is positive. This equals "cheapest hour
    then most expensive hour" whenever the cheapest hour comes first, and otherwise finds the
    best pair that respects the charge-before-discharge order. The cost is booked in the
    charge hour (negative) and the income in the discharge hour, so daily sums are exact.
    """
    out = pd.Series(0.0, index=day_ahead.index, name="arbitrage")
    day = pd.DatetimeIndex(day_ahead.index).tz_convert(tz).date
    for _, p in day_ahead.groupby(day):
        prices = p.to_numpy()
        if len(prices) < 2:
            continue
        # Cheapest price strictly before each hour: the best charge hour for discharging there.
        # Dividing by efficiency is monotonic, so the cheapest price is still the cheapest cost.
        best_before = np.minimum.accumulate(prices)[:-1]
        gain = prices[1:] * energy_mwh - best_before * energy_mwh / efficiency
        d = int(np.argmax(gain)) + 1
        if gain[d - 1] <= 0:
            continue
        c = int(np.argmin(prices[:d]))
        out[p.index[c]] = -prices[c] * energy_mwh / efficiency
        out[p.index[d]] = prices[d] * energy_mwh
    return out


def daily(hourly: pd.DataFrame, tz: str) -> pd.DataFrame:
    """Sum hourly EUR per market day, keeping only complete days and logging any dropped."""
    local = pd.DatetimeIndex(hourly.index).tz_convert(tz)
    day_start = local.normalize()
    # Calendar-aware day length: 23 h and 25 h on the DST change days, 24 h otherwise.
    expected = ((day_start + pd.DateOffset(days=1)) - day_start) / pd.Timedelta(hours=1)
    counts = pd.Series(1, index=local.date).groupby(level=0).sum()
    expected_per_day = pd.Series(np.asarray(expected), index=local.date).groupby(level=0).first()
    complete = counts[counts == expected_per_day].index
    dropped = len(counts) - len(complete)
    if dropped:
        # Rule 6: only the period edges can be partial (validation proved no gaps), and a
        # partial day would understate that day's revenue and bias the best-of choice.
        log.info("dropped %d incomplete market day(s) at the period edges", dropped)
    sums = hourly.groupby(local.date).sum()
    sums.index = pd.to_datetime(sums.index)
    sums.index.name = "market_day"
    return sums.loc[pd.to_datetime(complete)]


def best_of(days: pd.DataFrame) -> pd.DataFrame:
    """Add the daily best strategy: a perfect-foresight upper bound, not an achievable result."""
    return days.assign(best_of=days.max(axis=1), best_strategy=days.idxmax(axis=1))


def monthly(days: pd.DataFrame, power_mw: float) -> pd.DataFrame:
    """Monthly revenue per strategy in EUR per MW of installed battery power."""
    numeric = days.select_dtypes("number")
    return numeric.groupby(pd.DatetimeIndex(numeric.index).to_period("M")).sum() / power_mw


def annualised(days: pd.DataFrame, power_mw: float) -> pd.Series:
    """Revenue per strategy scaled to EUR/MW/year from however many complete days exist."""
    numeric = days.select_dtypes("number")
    return numeric.sum() / len(numeric) * 365 / power_mw

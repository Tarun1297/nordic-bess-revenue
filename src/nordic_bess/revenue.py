"""Battery revenue strategies as pure functions: prices in, EUR out, no I/O."""

import logging
from datetime import date, timedelta

import numpy as np
import pandas as pd

from nordic_bess import config

log = logging.getLogger(__name__)


def market_window_utc(first: date, last: date, tz: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    """UTC bounds [start, end) of the whole market days first..last (inclusive) in tz.

    The raw API files are UTC months, so extraction needs the market period in UTC.
    Example: 2022-01-01 in CET starts at 2021-12-31 23:00 UTC.
    """
    start = pd.Timestamp(first).tz_localize(tz).tz_convert("UTC")
    end = pd.Timestamp(last + timedelta(days=1)).tz_localize(tz).tz_convert("UTC")
    return start, end


def max_fcr_bids(power_mw: float, energy_mwh: float) -> tuple[float, float]:
    """Largest compliant (FCR-N, stacked FCR-D per direction) bids in MW, per Table 10.

    FCR-N needs FCR_N_POWER_PER_MW of power and FCR_N_ENERGY_H of energy in *both* directions
    per MW. Stacked FCR-D up + down needs, in each direction, 1 MW for its own product plus
    FCR_D_OPPOSITE_POWER_SHARE for the other, and FCR_D_ENERGY_H of energy each way.
    The binding limit (power or energy) sets the bid.
    """
    fcr_n = min(power_mw / config.FCR_N_POWER_PER_MW, energy_mwh / (2 * config.FCR_N_ENERGY_H))
    fcr_d = min(
        power_mw / (1 + config.FCR_D_OPPOSITE_POWER_SHARE),
        energy_mwh / (2 * config.FCR_D_ENERGY_H),
    )
    return fcr_n, fcr_d


def to_wide(prices: pd.DataFrame) -> pd.DataFrame:
    """Pivot the tidy price table to one column per product, indexed by CET hour."""
    return prices.pivot(index="timestamp_cet", columns="product", values="price")


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


def arbitrage(
    day_ahead: pd.Series, power_mw: float, energy_mwh: float, efficiency: float
) -> pd.Series:
    """EUR per market day from one perfect-foresight charge/discharge cycle on day-ahead prices.

    The battery charges at full power for energy/power hours, buying `energy_mwh`, and later
    sells the `energy_mwh * efficiency` it stored over a block of the same length. A block is a
    run of consecutive price intervals (four 15-minute prices, or one hourly price before
    1 Oct 2025), priced at its average. The charge block must end before the discharge block
    starts, and the day trades only if the gain is positive. The index must be tz-aware market
    time: its calendar date is the market day.
    """
    duration = pd.Timedelta(hours=energy_mwh / power_mw)
    out: dict[object, float] = {}
    idx = pd.DatetimeIndex(day_ahead.index)
    for day, p in day_ahead.groupby(idx.date):
        # One resolution per day: the 15-minute switch happened at a market-day boundary.
        step = pd.Series(pd.DatetimeIndex(p.index)).diff().min()
        k = max(1, round(duration / step)) if pd.notna(step) else 1
        prices = p.to_numpy(dtype=float)
        if len(prices) < 2 * k:
            out[day] = 0.0
            continue
        # Average price of the block starting at each interval.
        block = np.convolve(prices, np.ones(k) / k, mode="valid")
        # For a discharge block starting at j, the cheapest charge block must start at j-k or
        # earlier, so it ends before discharging begins.
        cheapest_charge = np.minimum.accumulate(block)[: len(block) - k]
        gain = energy_mwh * efficiency * block[k:] - energy_mwh * cheapest_charge
        out[day] = max(float(gain.max()), 0.0)
    result = pd.Series(out, name="arbitrage", dtype=float)
    result.index = pd.to_datetime(result.index)
    result.index.name = "market_day"
    return result


def trade_room_mwh(energy_mwh: float, power_mw: float, fcr_d_bid_mw: float) -> float:
    """Energy a battery can trade while keeping its FCR-D energy reserve in both directions.

    Table 10 reserves FCR_D_ENERGY_H of full activation per MW of FCR-D in each direction, so
    a battery offering FCR-D up and down must always hold that much energy and that much room.
    What is left between the two reserves can be traded without ever breaking FCR-D. The trade
    happens in one hour, so it can also never exceed one hour at full power.
    """
    return max(0.0, min(power_mw * 1.0, energy_mwh - 2 * fcr_d_bid_mw * config.FCR_D_ENERGY_H))


def fcr_d_plus_trade(
    fcr_d_hourly: pd.Series, day_ahead_hourly: pd.Series, trade_mwh: float, efficiency: float
) -> pd.Series:
    """EUR per market day from FCR-D every hour except one charge hour and one later sell hour.

    In the two trading hours the battery leaves FCR-D and buys `trade_mwh`, then sells
    `trade_mwh * efficiency`. Trades are sized by trade_room_mwh, so FCR-D keeps its energy
    reserve in every other hour. Blocks are whole clock hours because FCR is bought per hour;
    day-ahead prices must be averaged to the hour first. A day trades only if the trade earns
    more than the FCR-D income it gives up, so this never earns less than FCR-D alone. Both
    inputs are indexed by the same tz-aware market-time hours; their date is the market day.
    """
    both = pd.concat({"fcr": fcr_d_hourly, "da": day_ahead_hourly}, axis=1, join="inner")
    out: dict[object, float] = {}
    for day, g in both.groupby(pd.DatetimeIndex(both.index).date):
        fcr, price = g["fcr"].to_numpy(dtype=float), g["da"].to_numpy(dtype=float)
        # Cheapest charge hour before each sell hour, counting the FCR-D income it gives up.
        charge_cost = np.minimum.accumulate(trade_mwh * price + fcr)[:-1]
        gain = efficiency * trade_mwh * price[1:] - fcr[1:] - charge_cost
        out[day] = float(fcr.sum()) + max(float(gain.max()) if len(gain) else 0.0, 0.0)
    result = pd.Series(out, name="fcr_d_plus_trade", dtype=float)
    result.index = pd.to_datetime(result.index)
    result.index.name = "market_day"
    return result


def hourly_mean(prices: pd.Series) -> pd.Series:
    """Average sub-hourly prices to clock hours, keeping the index's time zone.

    Grouping in UTC avoids the repeated autumn hour, where local hours are ambiguous.
    """
    tz = pd.DatetimeIndex(prices.index).tz
    utc_hour = pd.DatetimeIndex(prices.index).tz_convert("UTC").floor("h")
    hourly = prices.groupby(utc_hour).mean()
    hourly.index = pd.DatetimeIndex(hourly.index).tz_convert(tz)
    return hourly


def daily(hourly: pd.DataFrame) -> pd.DataFrame:
    """Sum hourly EUR per market day (the index's own calendar), keeping only complete days."""
    local = pd.DatetimeIndex(hourly.index)
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


def quarterly(days: pd.DataFrame, power_mw: float) -> pd.DataFrame:
    """Quarterly revenue per strategy in EUR per MW, with the number of days behind each sum.

    The day count shows a partial quarter at the period edges, so it is never read as a
    full quarter.
    """
    numeric = days.select_dtypes("number")
    by_quarter = numeric.groupby(pd.DatetimeIndex(numeric.index).to_period("Q"))
    return (by_quarter.sum() / power_mw).assign(days=by_quarter.size())


def annualised(days: pd.DataFrame, power_mw: float) -> pd.Series:
    """Revenue per strategy scaled to EUR/MW/year from however many complete days exist."""
    numeric = days.select_dtypes("number")
    return numeric.sum() / len(numeric) * 365 / power_mw

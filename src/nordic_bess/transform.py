"""Raw dataset rows to one tidy hourly CET table: timestamp_cet, product, price, unit (Silver)."""

import logging

import pandas as pd

from nordic_bess import config

log = logging.getLogger(__name__)

COLUMNS = ["timestamp_cet", "product", "price", "unit"]
HOUR = pd.Timedelta(hours=1)


def tidy(raw: pd.DataFrame, product: str, unit: str) -> pd.DataFrame:
    """Turn raw API rows for one product into the tidy schema, hourly, in market time (CET).

    Raw rows are UTC (as the API returns them). Hourly averaging happens in UTC first, because
    rounding to the hour in local time hits an ambiguous hour on the autumn DST change.
    """
    start = pd.to_datetime(raw["startTime"], utc=True, format="ISO8601")
    length = pd.to_datetime(raw["endTime"], utc=True, format="ISO8601") - start
    df = pd.DataFrame(
        {
            # Validation already proved these are explicit UTC; parsing keeps them tz-aware.
            "timestamp_cet": start,
            "product": product,
            "price": raw["value"].astype(float),
            "unit": unit,
        }
    )
    if (length < HOUR).any():
        df = _to_hourly(df, length)
    # Market time from here on: CET/CEST (Europe/Berlin), the calendar Fingrid's FCR market
    # days use. Still tz-aware, so the repeated autumn hour stays unambiguous.
    df["timestamp_cet"] = df["timestamp_cet"].dt.tz_convert(config.MARKET_TZ)
    return df.sort_values("timestamp_cet", ignore_index=True)


def _to_hourly(df: pd.DataFrame, length: pd.Series) -> pd.DataFrame:
    """Average sub-hourly prices into hourly prices, logging how many rows were merged.

    Mean is the right rule for both price types in this project. A capacity price in EUR/MW/h is
    already a per-hour rate, so holding 1 MW for four quarters at rates a-d earns mean(a-d) for
    the hour. An energy price in EUR/MWh averaged over quarters is the price of a flat 1 MW
    delivery across the hour. Summing would overstate both by up to 4x.
    """
    out = (
        df.assign(timestamp_cet=df["timestamp_cet"].dt.floor("h"), length=length)
        .groupby(["timestamp_cet", "product", "unit"], as_index=False)
        .agg(price=("price", "mean"), covered=("length", "sum"))
    )
    # Coverage, not row count, finds partial hours, so already-hourly rows in a mixed series
    # (e.g. the month Fingrid switches to 15 min) are never miscounted as incomplete.
    partial = int((out["covered"] < HOUR).sum())
    # Rule 6: report the change. Partial hours are averaged over the quarters that exist.
    log.info(
        "aggregated %d rows into %d hourly prices (%d hours only partly covered)",
        len(df),
        len(out),
        partial,
    )
    return out[COLUMNS]

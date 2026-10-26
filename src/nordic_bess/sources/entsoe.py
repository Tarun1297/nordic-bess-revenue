"""ENTSO-E client: Finnish day-ahead prices in monthly chunks to data/raw/ (Bronze layer).

API facts, from the Restful API guide (sitemap: https://transparencyplatform.zendesk.com/hc/en-us/
articles/15692855254548): endpoint https://web-api.tp.entsoe.eu/api; the token is the
`securityToken` query parameter; request and response times are UTC; day-ahead prices allow at
most one year per request; 400 requests per minute per token. entsoe-py 0.8.1 builds the request
(documentType A44, Finland 10YFI-1--------U), parses the XML, and fills A03 curve gaps by
repeating the last value, which is how that curve type encodes unchanged prices.
"""

import logging
import os
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv
from entsoe.entsoe import EntsoePandasClient
from entsoe.exceptions import NoMatchingDataError

from nordic_bess import config
from nordic_bess.sources.fingrid import month_starts, next_month

log = logging.getLogger(__name__)

# Same raw shape as Fingrid's, so validation and transform work on both sources unchanged.
RAW_COLUMNS = ["startTime", "endTime", "value"]


def api_key() -> str | None:
    """Return the ENTSO-E token from .env, or None when it has not been added yet."""
    load_dotenv(config.ENV_FILE)
    return os.getenv("ENTSOE_API_KEY") or None


def to_raw(prices: pd.Series, start_utc: pd.Timestamp, end_utc: pd.Timestamp) -> pd.DataFrame:
    """Shape entsoe-py's price series into raw rows (UTC start, UTC end, EUR/MWh) in [start, end).

    entsoe-py returns only interval starts, so each interval ends where the next one begins: one
    hour before 1 Oct 2025, 15 minutes after. The full series, including the boundary point it
    returns at `end`, sets that length; the last point repeats the step before it.
    """
    s = prices.sort_index()
    starts = pd.DatetimeIndex(s.index).tz_convert("UTC")
    if len(starts) < 2:
        raise ValueError("need at least two price points to tell their resolution")
    steps = starts[1:] - starts[:-1]
    ends = starts + pd.TimedeltaIndex(steps.append(steps[-1:]))
    keep = (starts >= start_utc) & (starts < end_utc)
    dropped = int((~keep).sum())
    if dropped:
        # Rule 6: say what was cut. entsoe-py includes the point at `end` (inclusive truncate).
        log.info("dropped %d price point(s) outside [%s, %s)", dropped, start_utc, end_utc)
    fmt = "%Y-%m-%dT%H:%M:%S.000Z"
    return pd.DataFrame(
        {
            "startTime": starts[keep].strftime(fmt),
            "endTime": ends[keep].strftime(fmt),
            "value": s.to_numpy(dtype=float)[keep],
        }
    )


def fetch_month(client: EntsoePandasClient, month: date) -> pd.DataFrame:
    """Fetch one UTC month of Finnish day-ahead prices as raw rows (empty if none published)."""
    start = pd.Timestamp(datetime(month.year, month.month, 1, tzinfo=UTC))
    nxt = next_month(month)
    end = pd.Timestamp(datetime(nxt.year, nxt.month, 1, tzinfo=UTC))
    try:
        prices = client.query_day_ahead_prices(config.ENTSOE_AREA, start=start, end=end)
    except NoMatchingDataError:
        return pd.DataFrame(columns=RAW_COLUMNS)
    except requests.RequestException as e:
        # The token is a query parameter, so the request URL (and an HTTPError's message) holds
        # it. Re-raise with the status only, and drop the chained exception that carries the URL.
        status = getattr(e.response, "status_code", "no response")
        raise RuntimeError(f"ENTSO-E request for {month:%Y-%m} failed: HTTP {status}") from None
    df = to_raw(prices, start, end)
    df["area"] = config.ENTSOE_AREA
    df["fetched_at_utc"] = pd.Timestamp.now(tz="UTC")
    return df


def raw_path(month: date) -> Path:
    """Path of the raw parquet file for one month of day-ahead prices."""
    return config.DATA_RAW / f"entsoe_da_{config.ENTSOE_AREA}_{month:%Y_%m}.parquet"


def load_raw() -> pd.DataFrame:
    """Concatenate every monthly day-ahead raw file, in month order."""
    paths = sorted(config.DATA_RAW.glob(f"entsoe_da_{config.ENTSOE_AREA}_*.parquet"))
    if not paths:
        raise FileNotFoundError(f"no ENTSO-E raw files in {config.DATA_RAW}")
    return pd.concat((pd.read_parquet(p) for p in paths), ignore_index=True)


def download(key: str, first: date, last: date, refresh: bool = False) -> list[Path]:
    """Download day-ahead prices month by month, skipping months on disk unless refresh."""
    # entsoe-py logs full request parameters, token included, at DEBUG level: never show them.
    logging.getLogger("entsoe").setLevel(logging.WARNING)
    client = EntsoePandasClient(api_key=key)
    config.DATA_RAW.mkdir(parents=True, exist_ok=True)
    written = []
    for month in month_starts(first, last):
        path = raw_path(month)
        # Idempotent: re-runs cost no API calls; --refresh forces a new download.
        if path.exists() and not refresh:
            log.info("skip %s (exists)", path.name)
            continue
        df = fetch_month(client, month)
        if df.empty:
            # Not written, so the next run retries instead of caching a hole forever.
            log.warning("day-ahead %s: no prices published, nothing saved", month)
            continue
        df.to_parquet(path, index=False)
        log.info("wrote %s (%d rows)", path.name, len(df))
        written.append(path)
    return written

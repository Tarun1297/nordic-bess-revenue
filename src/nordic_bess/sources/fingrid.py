"""Fingrid open data client: downloads datasets in monthly chunks to data/raw/ (Bronze layer)."""

import argparse
import logging
import os
import time
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from nordic_bess import config

log = logging.getLogger(__name__)

RAW_COLUMNS = ["datasetId", "startTime", "endTime", "value"]

# ponytail: module-level throttle, fine for one sequential process; needs a lock if ever threaded.
_last_request = 0.0


def make_session() -> requests.Session:
    """Build a session with the API key header and retries for 429 and 5xx."""
    load_dotenv(config.ENV_FILE)
    key = os.getenv("FINGRID_API_KEY")
    if not key:
        raise RuntimeError(f"FINGRID_API_KEY is not set; add it to {config.ENV_FILE}")
    session = requests.Session()
    # Header, not query string: Fingrid's instructions require it, and it keeps the key out of
    # URLs and logs.
    session.headers["x-api-key"] = key
    # Overload behaviour is not documented, so retry 429/5xx with exponential backoff
    # (2, 4, 8, 16, 32 s) and honour Retry-After if the server sends one.
    retry = Retry(
        total=5,
        backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        respect_retry_after_header=True,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def _throttle() -> None:
    """Sleep so consecutive requests stay at least FINGRID_MIN_INTERVAL_S apart."""
    global _last_request
    wait = config.FINGRID_MIN_INTERVAL_S - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.monotonic()


def fetch_dataset(
    dataset_id: int,
    start: datetime,
    end: datetime,
    session: requests.Session | None = None,
) -> pd.DataFrame:
    """Fetch every row of one dataset between two UTC datetimes, following pagination."""
    session = session or make_session()
    params: dict[str, str | int] = {
        # Explicit Z suffix so the API never has to guess the time zone.
        "startTime": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        # Observed 2026-10-25: rows are returned when endTime <= this value, so a chunk
        # [month start, next month start] does not overlap the next chunk.
        "endTime": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pageSize": config.FINGRID_PAGE_SIZE,
        # API default is desc; asc keeps raw files in time order for easier inspection.
        "sortBy": "startTime",
        "sortOrder": "asc",
    }
    rows: list[dict[str, object]] = []
    page: int | None = 1
    while page is not None:
        _throttle()
        resp = session.get(
            f"{config.FINGRID_BASE_URL}/datasets/{dataset_id}/data",
            params={**params, "page": page},
            timeout=60,
        )
        resp.raise_for_status()
        body = resp.json()
        rows.extend(body["data"])
        page = body["pagination"]["nextPage"]  # null on the last page

    # Bronze layer keeps the API's own columns untouched; parsing happens in transform.
    df = pd.DataFrame(rows, columns=RAW_COLUMNS)
    df["dataset_id"] = dataset_id
    df["fetched_at_utc"] = pd.Timestamp.now(tz="UTC")
    return df


def next_month(d: date) -> date:
    """First day of the month after d (December rolls over to January)."""
    return date(d.year + d.month // 12, d.month % 12 + 1, 1)


def month_starts(first: date, last: date) -> list[date]:
    """Return the first day of every month from first's month to last's month, inclusive."""
    months = []
    current = first.replace(day=1)
    while current <= last:
        months.append(current)
        current = next_month(current)
    return months


def raw_path(dataset_id: int, month: date) -> Path:
    """Path of the raw parquet file for one dataset and month."""
    return config.DATA_RAW / f"fingrid_{dataset_id}_{month:%Y_%m}.parquet"


def download(
    dataset_ids: list[int],
    first: date,
    last: date,
    refresh: bool = False,
) -> list[Path]:
    """Download each dataset month by month, skipping months already on disk unless refresh."""
    config.DATA_RAW.mkdir(parents=True, exist_ok=True)
    session = make_session()
    written = []
    for dataset_id in dataset_ids:
        for month in month_starts(first, last):
            path = raw_path(dataset_id, month)
            # Idempotent: re-runs cost no API calls; --refresh forces a new download.
            if path.exists() and not refresh:
                log.info("skip %s (exists)", path.name)
                continue
            nxt = next_month(month)
            df = fetch_dataset(
                dataset_id,
                datetime(month.year, month.month, 1, tzinfo=UTC),
                datetime(nxt.year, nxt.month, 1, tzinfo=UTC),
                session,
            )
            if df.empty:
                # Not written, so the next run retries instead of caching a hole forever.
                log.warning("dataset %s %s: API returned no rows, nothing saved", dataset_id, month)
                continue
            df.to_parquet(path, index=False)
            log.info("wrote %s (%d rows)", path.name, len(df))
            written.append(path)
    return written


def main() -> None:
    """CLI entry point: download all configured Fingrid datasets for the configured period."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="re-download existing months")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    download(list(config.FINGRID_DATASETS), config.PERIOD_START, config.PERIOD_END, args.refresh)


if __name__ == "__main__":
    main()

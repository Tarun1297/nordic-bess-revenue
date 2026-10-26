"""CLI that runs the pipeline end to end; inputs come from params.toml, steps stay thin."""

import argparse
import dataclasses
import logging
import time
from collections.abc import Callable
from datetime import date
from functools import partial
from pathlib import Path

import pandas as pd

from nordic_bess import config, report, revenue, transform, validate
from nordic_bess.sources import entsoe, fingrid

log = logging.getLogger(__name__)


def raw_span_utc(params: config.Params) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Whole UTC months [start, end) that contain every hour of the trend's market days.

    Raw files are UTC months, while periods are CET market days that start an hour or two
    earlier in UTC. E.g. 1 Jan 2022 CET begins 31 Dec 2021 23:00 UTC, so December 2021 is
    needed for 2022 to be a full year.
    """
    start, end = revenue.market_window_utc(params.trend_start, params.end, config.MARKET_TZ)
    first_month = start.date().replace(day=1)
    after_last = fingrid.next_month((end - pd.Timedelta(hours=1)).date())
    return pd.Timestamp(first_month, tz="UTC"), pd.Timestamp(after_last, tz="UTC")


def raw_sources(with_day_ahead: bool) -> dict[str, Callable[[], pd.DataFrame]]:
    """Every raw dataset by product name, with the function that loads it from data/raw/."""
    sources: dict[str, Callable[[], pd.DataFrame]] = {
        name: partial(fingrid.load_raw, dataset_id)
        for dataset_id, name in config.FINGRID_DATASETS.items()
    }
    if with_day_ahead:
        sources[config.DAY_AHEAD] = entsoe.load_raw
    return sources


def extract(params: config.Params, refresh: bool, entsoe_key: str | None) -> None:
    """Download raw months covering the trend period (skips months already on disk)."""
    start, end = raw_span_utc(params)
    last = (end - pd.Timedelta(days=1)).date()
    fingrid.download(list(config.FINGRID_DATASETS), start.date(), last, refresh)
    if entsoe_key:
        entsoe.download(entsoe_key, start.date(), last, refresh)


def validate_raw(
    params: config.Params, with_day_ahead: bool
) -> dict[str, list[validate.CheckResult]]:
    """Validate every raw dataset, log a summary, and raise if any critical check failed."""
    # Validate whole UTC months so every UTC day in the span is complete by construction.
    start_utc, end_utc = raw_span_utc(params)
    report = {}
    for name, load in raw_sources(with_day_ahead).items():
        raw = load()
        # Raw files may hold more months than this run asks for; check only the requested span.
        starts = pd.to_datetime(raw["startTime"], utc=True, format="ISO8601")
        results = validate.run_checks(
            raw[(starts >= start_utc) & (starts < end_utc)], start_utc, end_utc
        )
        report[name] = results
        for r in results:
            level = logging.INFO if r.passed else logging.WARNING
            status = "PASS" if r.passed else ("FAIL" if r.critical else "WARN")
            log.log(
                level,
                "%-10s %-22s %s rows=%-4d %s",
                name,
                r.name,
                status,
                r.rows_affected,
                r.detail,
            )
    critical = [
        f"{name}: {r.name}"
        for name, rs in report.items()
        for r in rs
        if r.critical and not r.passed
    ]
    # Fail loudly: wrong time zones or duplicates would silently corrupt every revenue number.
    if critical:
        raise RuntimeError(f"critical validation failures: {critical}")
    return report


def transform_raw(with_day_ahead: bool) -> pd.DataFrame:
    """Build data/clean/prices.parquet: one tidy CET table across all products.

    FCR prices are hourly. Day-ahead prices keep their own resolution (15 minutes from
    1 Oct 2025), because arbitrage on 15-minute prices was the chosen method.
    """
    tables = [
        transform.tidy(load(), name, config.FCR_UNIT)
        for name, load in raw_sources(with_day_ahead=False).items()
    ]
    if with_day_ahead:
        tables.append(
            transform.tidy(entsoe.load_raw(), config.DAY_AHEAD, config.DAY_AHEAD_UNIT, hourly=False)
        )
    prices = pd.concat(tables, ignore_index=True)
    config.DATA_CLEAN.mkdir(parents=True, exist_ok=True)
    prices.to_parquet(config.DATA_CLEAN / "prices.parquet", index=False)
    log.info(
        "wrote prices.parquet (%d rows, %d products)", len(prices), prices["product"].nunique()
    )
    return prices


def compute_revenue(params: config.Params, with_day_ahead: bool) -> pd.DataFrame:
    """Build data/clean/revenue_daily.parquet: EUR per market day for each strategy."""
    fcr_n_mw, fcr_d_mw = revenue.max_fcr_bids(params.power_mw, params.energy_mwh)
    log.info("compliant bids: FCR-N %.3f MW, FCR-D up+down %.3f MW each", fcr_n_mw, fcr_d_mw)
    prices = pd.read_parquet(config.DATA_CLEAN / "prices.parquet")
    is_da = prices["product"] == config.DAY_AHEAD
    wide = revenue.to_wide(prices[~is_da])
    # The index is CET, so date strings select whole market days directly.
    wide = wide.loc[str(params.trend_start) : str(params.end)]
    hourly = pd.concat(
        [revenue.fcr_n_only(wide, fcr_n_mw), revenue.fcr_d_stacked(wide, fcr_d_mw)], axis=1
    )
    days = revenue.daily(hourly)
    if with_day_ahead:
        da = prices[is_da].set_index("timestamp_cet")["price"].sort_index()
        da = da.loc[str(params.trend_start) : str(params.end)]
        arb = revenue.arbitrage(
            da, params.power_mw, params.energy_mwh, params.round_trip_efficiency
        )
        days = days.join(arb, how="left")
        missing = int(days["arbitrage"].isna().sum())
        if missing:
            # Rule 6: a day without day-ahead prices earns nothing from arbitrage; say how many.
            log.warning(
                "%d market day(s) have no day-ahead prices; arbitrage counted as 0", missing
            )
            days["arbitrage"] = days["arbitrage"].fillna(0.0)
    else:
        log.info("ENTSOE_API_KEY not set: day-ahead arbitrage skipped")
    # Availability scales every strategy equally: an unavailable hour earns nothing anywhere.
    days = revenue.best_of(days * params.availability)
    days.to_parquet(config.DATA_CLEAN / "revenue_daily.parquet")
    headline = days.loc[str(params.start) : str(params.end)]
    for strategy, eur in revenue.annualised(headline, params.power_mw).items():
        log.info("headline %-14s %10.0f EUR/MW/year", strategy, eur)
    return days


def timed[T](name: str, step: Callable[[], T]) -> T:
    """Run one pipeline step, log how long it took, and pass its result on."""
    t0 = time.perf_counter()
    result = step()
    log.info("step %s done in %.1f s", name, time.perf_counter() - t0)
    return result


def main() -> None:
    """Load inputs, apply CLI overrides, and run each pipeline step with timing."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=config.PARAMS_FILE, help="inputs file")
    parser.add_argument("--start", type=date.fromisoformat, help="headline start, YYYY-MM-DD")
    parser.add_argument("--end", type=date.fromisoformat, help="headline end (inclusive)")
    parser.add_argument("--refresh", action="store_true", help="re-download existing raw months")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    params = config.load_params(args.config)
    overrides = {k: v for k, v in (("start", args.start), ("end", args.end)) if v is not None}
    if overrides:
        params = dataclasses.replace(params, **overrides)
        # Re-check ordering: CLI dates bypass the file validation.
        if not params.trend_start <= params.start <= params.end:
            parser.error("need trend_start <= --start <= --end")
    log.info("inputs: %s", params)

    # The pipeline runs without the ENTSO-E key; arbitrage is then skipped with a log line.
    entsoe_key = entsoe.api_key()
    with_da = entsoe_key is not None
    timed("extract", partial(extract, params, args.refresh, entsoe_key))
    quality = timed("validate", partial(validate_raw, params, with_da))
    prices = timed("transform", partial(transform_raw, with_da))
    days = timed("revenue", partial(compute_revenue, params, with_da))
    timed("report", partial(report.write_outputs, days, prices, params, quality))


if __name__ == "__main__":
    main()

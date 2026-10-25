"""CLI that runs the pipeline end to end; each step is a function so the CLI stays thin."""

import argparse
import logging
import time
from collections.abc import Callable
from datetime import timedelta
from functools import partial

import pandas as pd

from nordic_bess import config, transform, validate
from nordic_bess.sources import fingrid

log = logging.getLogger(__name__)


def extract(refresh: bool) -> None:
    """Download raw Fingrid data for the configured period (skips months already on disk)."""
    fingrid.download(list(config.FINGRID_DATASETS), config.PERIOD_START, config.PERIOD_END, refresh)


def validate_raw() -> dict[str, list[validate.CheckResult]]:
    """Validate every raw dataset, log a summary, and raise if any critical check failed."""
    start_utc = pd.Timestamp(config.PERIOD_START, tz="UTC")
    # PERIOD_END is an inclusive date, so the exclusive bound is the following midnight.
    end_utc = pd.Timestamp(config.PERIOD_END + timedelta(days=1), tz="UTC")
    report = {}
    for dataset_id, name in config.FINGRID_DATASETS.items():
        results = validate.run_checks(fingrid.load_raw(dataset_id), start_utc, end_utc)
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


def transform_raw() -> pd.DataFrame:
    """Build data/clean/prices.parquet: one tidy hourly UTC table across all products."""
    prices = pd.concat(
        [
            transform.tidy(fingrid.load_raw(dataset_id), product, config.FCR_UNIT)
            for dataset_id, product in config.FINGRID_DATASETS.items()
        ],
        ignore_index=True,
    )
    config.DATA_CLEAN.mkdir(parents=True, exist_ok=True)
    prices.to_parquet(config.DATA_CLEAN / "prices.parquet", index=False)
    log.info(
        "wrote prices.parquet (%d rows, %d products)", len(prices), prices["product"].nunique()
    )
    return prices


def main() -> None:
    """Parse arguments and run each pipeline step with timing."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="re-download existing raw months")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    steps: list[tuple[str, Callable[[], object]]] = [
        ("extract", partial(extract, args.refresh)),
        ("validate", validate_raw),
        ("transform", transform_raw),
    ]
    for step_name, step in steps:
        t0 = time.perf_counter()
        step()
        log.info("step %s done in %.1f s", step_name, time.perf_counter() - t0)


if __name__ == "__main__":
    main()

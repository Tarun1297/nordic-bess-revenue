"""Pure data-quality checks on one raw dataset: they flag problems and never change the data."""

from dataclasses import dataclass

import pandas as pd

from nordic_bess import config

# Fingrid returns e.g. "2026-09-30T20:00:00.000Z"; both forms below mean UTC explicitly.
UTC_SUFFIXES = ("Z", "+00:00")


@dataclass(frozen=True)
class CheckResult:
    """Outcome of one check; critical failures stop the pipeline, the rest are logged warnings."""

    name: str
    passed: bool
    rows_affected: int
    detail: str
    critical: bool = False


def _times(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Parse the raw start and end strings to tz-aware UTC timestamps."""
    return (
        pd.to_datetime(df["startTime"], utc=True, format="ISO8601"),
        pd.to_datetime(df["endTime"], utc=True, format="ISO8601"),
    )


def check_timestamps_utc(df: pd.DataFrame) -> CheckResult:
    """Every start and end time parses and carries an explicit UTC marker."""
    bad = pd.Series(False, index=df.index)
    for col in ("startTime", "endTime"):
        text = df[col].astype(str)
        parsed = pd.to_datetime(text, utc=True, errors="coerce", format="ISO8601")
        # "+02:00" would still parse (silently shifted to UTC), so insist on an explicit UTC
        # marker: a different offset means the source changed and needs a human look.
        bad |= parsed.isna() | ~text.str.endswith(UTC_SUFFIXES)
    n = int(bad.sum())
    detail = "all timestamps are explicit UTC" if n == 0 else f"first bad: {df.loc[bad].iloc[0, 1]}"
    return CheckResult("timestamps_utc", n == 0, n, detail, critical=True)


def check_duplicates(df: pd.DataFrame) -> CheckResult:
    """No interval appears twice, because a duplicate would double-count revenue."""
    start, _ = _times(df)
    dup = start.duplicated(keep=False)
    n = int(dup.sum())
    detail = (
        "none" if n == 0 else f"{start[dup].nunique()} intervals repeated, first {start[dup].min()}"
    )
    return CheckResult("no_duplicates", n == 0, n, detail, critical=True)


def check_resolution(df: pd.DataFrame) -> CheckResult:
    """One interval length across the whole period, e.g. flags a switch from 1 h to 15 min.

    Unit is not checked here: rows carry no unit field, so it is fixed per dataset in config
    (EUR/MW per hour, from Fingrid's dataset pages).
    """
    start, end = _times(df)
    lengths = (end - start).value_counts()
    passed = len(lengths) == 1
    detail = ", ".join(f"{length}: {count} rows" for length, count in lengths.items())
    # Rows outside the dominant resolution are the ones that would need resampling.
    return CheckResult("single_resolution", passed, int(len(df) - lengths.iloc[0]), detail)


def check_daily_coverage(df: pd.DataFrame) -> CheckResult:
    """Each UTC day is fully covered: 24 hourly or 96 quarter-hour intervals.

    Grouping by UTC date, not Finnish local date, is what keeps DST days normal: the 23- and
    25-hour local days are still exactly 24 hours in UTC. Summing interval lengths instead of
    counting rows works for 1 h and 15 min data alike.
    """
    start, end = _times(df)
    covered = (end - start).groupby(start.dt.date).sum()
    counts = start.groupby(start.dt.date).size()
    bad = covered[covered != pd.Timedelta(days=1)]
    detail = (
        "every UTC day covers 24 h"
        if bad.empty
        else "; ".join(f"{day}: {counts[day]} intervals, {covered[day]}" for day in bad.index[:10])
    )
    return CheckResult("full_utc_days", bad.empty, len(bad), detail)


def check_missing_intervals(
    df: pd.DataFrame, start_utc: pd.Timestamp, end_utc: pd.Timestamp
) -> CheckResult:
    """List intervals in [start_utc, end_utc) that are absent or have no value, by UTC date."""
    start, end = _times(df)
    step = (end - start).mode().iloc[0]
    expected = pd.date_range(start_utc, end_utc, freq=step, inclusive="left")
    # A row with a null value is as unusable as no row, so both count as missing.
    present = pd.DatetimeIndex(start[df["value"].notna()])
    missing = expected.difference(present)
    by_date = pd.Series(missing.date).value_counts().sort_index()
    detail = "none" if missing.empty else "; ".join(f"{d}: {c}" for d, c in by_date.items())
    return CheckResult("no_missing_intervals", missing.empty, len(missing), detail)


def check_outliers(df: pd.DataFrame, quantile: float = config.OUTLIER_QUANTILE) -> CheckResult:
    """Flag (never drop) values above the given percentile so extreme hours get a second look."""
    threshold = df["value"].quantile(quantile)
    flagged = df[df["value"] > threshold]
    top = flagged.nlargest(3, "value")
    detail = f"threshold {threshold:.2f}; top: " + ", ".join(
        f"{v} at {t}" for v, t in zip(top["value"], top["startTime"], strict=True)
    )
    return CheckResult("outliers_flagged", flagged.empty, len(flagged), detail)


def run_checks(
    df: pd.DataFrame, start_utc: pd.Timestamp, end_utc: pd.Timestamp
) -> list[CheckResult]:
    """Run every check on one dataset; stop after a timestamp failure since the rest need it."""
    first = check_timestamps_utc(df)
    if not first.passed:
        return [first]
    return [
        first,
        check_duplicates(df),
        check_resolution(df),
        check_daily_coverage(df),
        check_missing_intervals(df, start_utc, end_utc),
        check_outliers(df),
    ]

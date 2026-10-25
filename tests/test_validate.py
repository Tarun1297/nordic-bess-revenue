from pathlib import Path

import numpy as np
import pandas as pd

from nordic_bess import validate

FIXTURES = Path(__file__).parent / "fixtures"
# The fixture covers three full UTC days around the spring DST change (Finland: 2026-03-29).
START = pd.Timestamp("2026-03-28", tz="UTC")
END = pd.Timestamp("2026-03-31", tz="UTC")


def sample() -> pd.DataFrame:
    return pd.read_csv(FIXTURES / "fingrid_317_2026-03-28_to_30.csv")


def test_timestamps_utc_pass() -> None:
    assert validate.check_timestamps_utc(sample()).passed


def test_timestamps_utc_fail_on_local_offset() -> None:
    df = sample()
    df.loc[5, "startTime"] = "2026-03-28T07:00:00.000+02:00"
    result = validate.check_timestamps_utc(df)
    assert not result.passed and result.critical and result.rows_affected == 1


def test_duplicates_pass() -> None:
    assert validate.check_duplicates(sample()).passed


def test_duplicates_fail() -> None:
    df = sample()
    result = validate.check_duplicates(pd.concat([df, df.iloc[[0]]]))
    assert not result.passed and result.critical and result.rows_affected == 2


def test_resolution_pass() -> None:
    assert validate.check_resolution(sample()).passed


def test_resolution_fail_on_15_min_row() -> None:
    df = sample()
    df.loc[0, "endTime"] = "2026-03-28T00:15:00.000Z"
    result = validate.check_resolution(df)
    assert not result.passed and result.rows_affected == 1


def test_daily_coverage_pass_on_dst_day() -> None:
    df = sample()
    # The local 23-hour day is still 24 rows in UTC.
    assert df["startTime"].str.startswith("2026-03-29").sum() == 24
    assert validate.check_daily_coverage(df).passed


def test_daily_coverage_fail_on_dropped_hour() -> None:
    result = validate.check_daily_coverage(sample().drop(index=30))
    assert not result.passed and result.rows_affected == 1
    assert "2026-03-29: 23 intervals" in result.detail


def test_missing_intervals_pass() -> None:
    assert validate.check_missing_intervals(sample(), START, END).passed


def test_missing_intervals_fail_on_gap_and_null() -> None:
    df = sample().drop(index=30)
    df.loc[50, "value"] = np.nan
    result = validate.check_missing_intervals(df, START, END)
    assert not result.passed and result.rows_affected == 2
    assert "2026-03-29: 1" in result.detail and "2026-03-30: 1" in result.detail


def test_outliers_pass_on_flat_prices() -> None:
    df = sample().assign(value=10.0)
    assert validate.check_outliers(df).passed


def test_outliers_flag_spike_without_dropping() -> None:
    df = sample().assign(value=10.0)
    df.loc[40, "value"] = 500.0
    result = validate.check_outliers(df)
    assert not result.passed and result.rows_affected == 1
    assert len(df) == 72  # flagged, never removed


def test_run_checks_stops_after_timestamp_failure() -> None:
    df = sample()
    df.loc[0, "startTime"] = "not a time"
    assert [r.name for r in validate.run_checks(df, START, END)] == ["timestamps_utc"]

from pathlib import Path

import pandas as pd
import pytest

from nordic_bess import transform

FIXTURES = Path(__file__).parent / "fixtures"


def raw_rows(starts: list[str], minutes: int, values: list[float]) -> pd.DataFrame:
    start = pd.to_datetime(starts, utc=True)
    end = start + pd.Timedelta(minutes=minutes)
    return pd.DataFrame(
        {
            "startTime": start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "endTime": end.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "value": values,
        }
    )


def test_hourly_fixture_passes_through_unchanged() -> None:
    raw = pd.read_csv(FIXTURES / "fingrid_317_2026-03-28_to_30.csv")
    df = transform.tidy(raw, "fcr_n", "EUR/MW/h")

    assert list(df.columns) == transform.COLUMNS
    assert len(df) == 72  # includes the DST day as 24 UTC hours
    assert str(df["timestamp_utc"].dt.tz) == "UTC"
    assert df["timestamp_utc"].is_monotonic_increasing
    assert df["price"].tolist() == raw["value"].tolist()  # no value altered
    assert set(df["product"]) == {"fcr_n"} and set(df["unit"]) == {"EUR/MW/h"}


def test_quarter_hours_are_averaged_including_negatives(caplog: pytest.LogCaptureFixture) -> None:
    starts = [f"2026-10-01T00:{m:02d}" for m in (0, 15, 30, 45)] + ["2026-10-01T01:00"]
    raw = raw_rows(starts, 15, [10.0, 20.0, -30.0, 40.0, 8.0])

    with caplog.at_level("INFO"):
        df = transform.tidy(raw, "day_ahead", "EUR/MWh")

    # Hand-calculated: (10 + 20 - 30 + 40) / 4 = 10; the lone 01:00 quarter stays 8.
    assert df["price"].tolist() == [10.0, 8.0]
    assert df["timestamp_utc"].tolist() == list(
        pd.to_datetime(["2026-10-01T00:00", "2026-10-01T01:00"], utc=True)
    )
    assert "aggregated 5 rows into 2 hourly prices (1 hours only partly covered)" in caplog.text


def test_mixed_resolution_does_not_flag_hourly_rows_as_partial(
    caplog: pytest.LogCaptureFixture,
) -> None:
    hourly = raw_rows(["2026-10-01T00:00"], 60, [5.0])
    quarters = raw_rows(
        [f"2026-10-01T01:{m:02d}" for m in (0, 15, 30, 45)], 15, [1.0, 2.0, 3.0, 4.0]
    )

    with caplog.at_level("INFO"):
        df = transform.tidy(pd.concat([hourly, quarters]), "fcr_n", "EUR/MW/h")

    assert df["price"].tolist() == [5.0, 2.5]
    assert "(0 hours only partly covered)" in caplog.text


def test_empty_input_gives_empty_table() -> None:
    df = transform.tidy(raw_rows([], 60, []), "fcr_n", "EUR/MW/h")
    assert df.empty and list(df.columns) == transform.COLUMNS

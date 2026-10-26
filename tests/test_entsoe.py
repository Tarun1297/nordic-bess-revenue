from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
import requests

from nordic_bess import transform, validate
from nordic_bess.sources import entsoe

FIXTURES = Path(__file__).parent / "fixtures"
HEL = "Europe/Helsinki"  # entsoe-py returns Finnish prices in the area's own time zone


def test_to_raw_trims_inclusive_end_and_sets_each_interval_length() -> None:
    # Two hourly points then two quarter-hours, plus the boundary point entsoe-py adds at `end`.
    idx = pd.DatetimeIndex(
        [
            "2025-09-30 22:00",
            "2025-09-30 23:00",
            "2025-10-01 00:00",
            "2025-10-01 00:15",
            "2025-10-01 00:30",
        ],
        tz="UTC",
    ).tz_convert(HEL)
    prices = pd.Series([10.0, -5.0, 20.0, 30.0, 99.0], index=idx)

    raw = entsoe.to_raw(
        prices,
        pd.Timestamp("2025-09-30 22:00", tz="UTC"),
        pd.Timestamp("2025-10-01 00:30", tz="UTC"),
    )

    assert raw["startTime"].tolist() == [
        "2025-09-30T22:00:00.000Z",
        "2025-09-30T23:00:00.000Z",
        "2025-10-01T00:00:00.000Z",
        "2025-10-01T00:15:00.000Z",
    ]
    # Each row ends where the next begins: 1 h, 1 h, then 15 min (set by the trimmed end point).
    assert raw["endTime"].tolist() == [
        "2025-09-30T23:00:00.000Z",
        "2025-10-01T00:00:00.000Z",
        "2025-10-01T00:15:00.000Z",
        "2025-10-01T00:30:00.000Z",
    ]
    assert raw["value"].tolist() == [10.0, -5.0, 20.0, 30.0]  # negative price kept


class FailingClient:
    """Raises the way requests does, with the token in the URL, like a real failed call."""

    def query_day_ahead_prices(self, *args: Any, **kwargs: Any) -> pd.Series:
        resp = requests.Response()
        resp.status_code = 401
        resp.url = "https://web-api.tp.entsoe.eu/api?securityToken=SECRET-TOKEN&documentType=A44"
        raise requests.HTTPError(f"401 Client Error for url: {resp.url}", response=resp)


def test_failed_request_never_exposes_the_token() -> None:
    with pytest.raises(RuntimeError) as err:
        entsoe.fetch_month(FailingClient(), date(2025, 10, 1))  # type: ignore[arg-type]
    assert "HTTP 401" in str(err.value)
    assert "SECRET-TOKEN" not in str(err.value)
    assert err.value.__cause__ is None and err.value.__suppress_context__  # URL not chained


def test_real_sample_across_the_15_minute_switch() -> None:
    # Real Finnish prices, 30 Sep to 2 Oct 2025 UTC: hourly, then 15-minute from 1 Oct CET.
    raw = pd.read_csv(FIXTURES / "entsoe_da_FI_2025-09-30_to_10-01.csv")
    start = pd.Timestamp("2025-09-30", tz="UTC")
    end = pd.Timestamp("2025-10-02", tz="UTC")
    results = {r.name: r for r in validate.run_checks(raw, start, end)}

    assert results["timestamps_utc"].passed and results["no_duplicates"].passed
    assert results["full_utc_days"].passed
    # Gap check follows each row's own step, so the switch is not mistaken for missing data.
    assert results["no_missing_intervals"].passed
    # The resolution change is real and expected: flagged, not failed.
    assert not results["single_resolution"].passed and not results["single_resolution"].critical

    tidy = transform.tidy(raw, "day_ahead", "EUR/MWh", hourly=False)
    steps = tidy["timestamp_cet"].diff().value_counts()
    assert set(steps.index) == {pd.Timedelta(hours=1), pd.Timedelta(minutes=15)}
    assert len(tidy) == len(raw)  # 15-minute prices kept, not averaged

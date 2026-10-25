from datetime import UTC, date, datetime
from typing import Any

import pytest

from nordic_bess import config
from nordic_bess.sources import fingrid


class FakeResponse:
    def __init__(self, body: dict[str, Any]) -> None:
        self.body = body

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict[str, Any]:
        return self.body


class FakeSession:
    """Serves canned pages so pagination is tested without calling the live API (rule 7)."""

    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self.pages = pages
        self.requested_pages: list[int] = []

    def get(self, url: str, params: dict[str, Any], timeout: int) -> FakeResponse:
        self.requested_pages.append(params["page"])
        return FakeResponse(self.pages[params["page"] - 1])


def row(hour: int, value: float) -> dict[str, Any]:
    return {
        "datasetId": 317,
        "startTime": f"2026-03-29T{hour:02d}:00:00.000Z",
        "endTime": f"2026-03-29T{hour + 1:02d}:00:00.000Z",
        "value": value,
    }


def test_fetch_dataset_follows_pagination(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "FINGRID_MIN_INTERVAL_S", 0)
    session = FakeSession(
        [
            {"data": [row(0, 10.0), row(1, -2.5)], "pagination": {"nextPage": 2}},
            {"data": [row(2, 7.0)], "pagination": {"nextPage": None}},
        ]
    )
    start, end = datetime(2026, 3, 29, tzinfo=UTC), datetime(2026, 3, 30, tzinfo=UTC)

    df = fingrid.fetch_dataset(317, start, end, session)  # type: ignore[arg-type]

    assert session.requested_pages == [1, 2]
    assert df["value"].tolist() == [10.0, -2.5, 7.0]  # negative price kept as-is
    assert list(df.columns) == [*fingrid.RAW_COLUMNS, "dataset_id", "fetched_at_utc"]
    assert (df["dataset_id"] == 317).all()
    assert str(df["fetched_at_utc"].dt.tz) == "UTC"


def test_month_starts_crosses_year_end() -> None:
    assert fingrid.month_starts(date(2025, 11, 15), date(2026, 1, 31)) == [
        date(2025, 11, 1),
        date(2025, 12, 1),
        date(2026, 1, 1),
    ]

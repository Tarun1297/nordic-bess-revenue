import pandas as pd
import pytest

from nordic_bess import revenue

UTC = "UTC"


def hours(start: str, n: int) -> pd.DatetimeIndex:
    return pd.date_range(start, periods=n, freq="h", tz=UTC)


def wide(fcr_n: list[float], up: list[float], down: list[float]) -> pd.DataFrame:
    idx = hours("2026-01-01", len(fcr_n))
    return pd.DataFrame({"fcr_n": fcr_n, "fcr_d_up": up, "fcr_d_down": down}, index=idx)


def test_to_wide_pivots_products() -> None:
    idx = hours("2026-01-01", 2)
    tidy = pd.DataFrame(
        {
            "timestamp_utc": list(idx) * 2,
            "product": ["fcr_n"] * 2 + ["fcr_d_up"] * 2,
            "price": [1.0, 2.0, 3.0, 4.0],
        }
    )
    assert revenue.to_wide(tidy).loc[idx[1]].to_dict() == {"fcr_d_up": 4.0, "fcr_n": 2.0}


def test_fcr_n_scales_price_by_bid_including_negative() -> None:
    # 3 hours at 0.5 MW: 10*0.5, -2*0.5, 5*0.5
    assert revenue.fcr_n_only(wide([10, -2, 5], [0] * 3, [0] * 3), 0.5).tolist() == [5, -1, 2.5]


def test_fcr_d_stacks_up_and_down() -> None:
    # (4+1)*0.5 = 2.5, (6+2)*0.5 = 4
    assert revenue.fcr_d_stacked(wide([0, 0], [4, 6], [1, 2]), 0.5).tolist() == [2.5, 4.0]


def test_arbitrage_charge_must_precede_discharge() -> None:
    # Cheapest hour (10) comes last, so it cannot be used. Best legal pair: charge at 50, sell at
    # 100 -> 100 - 50 / 0.88 = 43.1818...
    p = pd.Series([50.0, 100.0, 10.0], index=hours("2026-01-01", 3))
    out = revenue.arbitrage(p, 1.0, 0.88, UTC)
    assert out.tolist() == pytest.approx([-50 / 0.88, 100.0, 0.0])
    assert out.sum() == pytest.approx(43.1818, abs=1e-4)


def test_arbitrage_profits_from_negative_charge_price() -> None:
    # Paid 20 to charge, sell at 30 -> 30 + 20 / 0.88 = 52.7273
    p = pd.Series([-20.0, 30.0], index=hours("2026-01-01", 2))
    assert revenue.arbitrage(p, 1.0, 0.88, UTC).sum() == pytest.approx(52.7273, abs=1e-4)


def test_arbitrage_skips_unprofitable_day() -> None:
    # Falling prices: best pair 90 then 80 loses money, so no trade.
    p = pd.Series([100.0, 90.0, 80.0], index=hours("2026-01-01", 3))
    assert (revenue.arbitrage(p, 1.0, 0.88, UTC) == 0).all()


def test_arbitrage_one_cycle_per_day() -> None:
    # Two days; each trades once on its own prices: day 1: 60 - 30/0.88, day 2: 40 - 10/0.88.
    p = pd.Series([30.0, 60.0, 10.0, 40.0], index=hours("2026-01-01T22:00", 4))
    # Hours 22,23 UTC on 1 Jan; 00,01 UTC on 2 Jan.
    daily = revenue.arbitrage(p, 1.0, 0.88, UTC).groupby(p.index.date).sum()
    assert daily.tolist() == pytest.approx([60 - 30 / 0.88, 40 - 10 / 0.88])


def test_daily_drops_partial_day_and_keeps_dst_day(caplog: pytest.LogCaptureFixture) -> None:
    # Spring DST in CET: 2026-03-29 has 23 local hours (22:00 UTC 28 Mar to 22:00 UTC 29 Mar).
    idx = pd.date_range("2026-03-28T20:00", "2026-03-29T21:00", freq="h", tz=UTC)
    hourly = pd.DataFrame({"a": 1.0}, index=idx)
    with caplog.at_level("INFO"):
        days = revenue.daily(hourly, "Europe/Berlin")
    # 28 Mar local has only 2 of 24 hours -> dropped; 29 Mar has all 23 -> kept.
    assert days.index.tolist() == [pd.Timestamp("2026-03-29")]
    assert days["a"].tolist() == [23.0]
    assert "dropped 1 incomplete market day" in caplog.text


def test_best_of_picks_daily_maximum() -> None:
    days = pd.DataFrame(
        {"fcr_n": [30.0, 10.0], "fcr_d_stacked": [20.0, 25.0]},
        index=pd.to_datetime(["2026-01-01", "2026-01-02"]),
    )
    out = revenue.best_of(days)
    assert out["best_of"].tolist() == [30.0, 25.0]
    assert out["best_strategy"].tolist() == ["fcr_n", "fcr_d_stacked"]


def test_monthly_and_annualised() -> None:
    idx = pd.to_datetime(["2026-01-30", "2026-01-31", "2026-02-01"])
    days = pd.DataFrame({"s": [10.0, 20.0, 30.0], "label": ["x", "y", "z"]}, index=idx)
    m = revenue.monthly(days, power_mw=2.0)
    assert m["s"].tolist() == [15.0, 15.0]  # (10+20)/2, 30/2; text columns ignored
    # 60 EUR over 3 days on 2 MW -> 60 / 3 * 365 / 2 = 3650
    assert revenue.annualised(days, power_mw=2.0)["s"] == pytest.approx(3650.0)

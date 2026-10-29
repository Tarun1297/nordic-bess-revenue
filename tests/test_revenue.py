from datetime import date

import pandas as pd
import pytest

from nordic_bess import revenue

CET = "Europe/Berlin"


def hours(start: str, n: int) -> pd.DatetimeIndex:
    return pd.date_range(start, periods=n, freq="h", tz=CET)


def wide(fcr_n: list[float], up: list[float], down: list[float]) -> pd.DataFrame:
    idx = hours("2026-01-01", len(fcr_n))
    return pd.DataFrame({"fcr_n": fcr_n, "fcr_d_up": up, "fcr_d_down": down}, index=idx)


def test_market_window_covers_whole_cet_days_in_utc() -> None:
    start, end = revenue.market_window_utc(date(2022, 1, 1), date(2026, 9, 30), "Europe/Berlin")
    assert start == pd.Timestamp("2021-12-31T23:00", tz="UTC")  # winter: CET = UTC+1
    assert end == pd.Timestamp("2026-09-30T22:00", tz="UTC")  # summer: CEST = UTC+2


def test_max_fcr_bids_for_one_hour_battery() -> None:
    # FCR-N: energy-bound, 1 MWh / (2 x 1 h) = 0.5. FCR-D: power-bound, 1 MW / 1.2 = 0.833.
    assert revenue.max_fcr_bids(1.0, 1.0) == pytest.approx((0.5, 1 / 1.2))


def test_max_fcr_bids_matches_fingrid_worked_example() -> None:
    # Technical Requirements v1.1 simulate "a 1.34 MW / 2 MWh battery" providing 1 MW of FCR-N.
    fcr_n, _ = revenue.max_fcr_bids(1.34, 2.0)
    assert fcr_n == pytest.approx(1.0)


def test_to_wide_pivots_products() -> None:
    idx = hours("2026-01-01", 2)
    tidy = pd.DataFrame(
        {
            "timestamp_cet": list(idx) * 2,
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


def quarters(start: str, n: int) -> pd.DatetimeIndex:
    return pd.date_range(start, periods=n, freq="15min", tz=CET)


def test_arbitrage_charge_must_precede_discharge() -> None:
    # Hourly, 1 MW / 1 MWh: the cheapest hour (10) comes last, so it can't be used. Best legal
    # pair: buy 1 MWh at 50, sell 0.88 MWh at 100 -> 0.88 x 100 - 50 = 38.
    p = pd.Series([50.0, 100.0, 10.0], index=hours("2026-01-01", 3))
    assert revenue.arbitrage(p, 1.0, 1.0, 0.88).tolist() == pytest.approx([38.0])


def test_arbitrage_profits_from_negative_charge_price() -> None:
    # Paid 20 to take 1 MWh, sell 0.88 MWh at 30 -> 26.4 + 20 = 46.4.
    p = pd.Series([-20.0, 30.0], index=hours("2026-01-01", 2))
    assert revenue.arbitrage(p, 1.0, 1.0, 0.88).tolist() == pytest.approx([46.4])


def test_arbitrage_skips_unprofitable_day() -> None:
    # Falling prices: the best legal pair (90 then 80) loses money, so no trade.
    p = pd.Series([100.0, 90.0, 80.0], index=hours("2026-01-01", 3))
    assert revenue.arbitrage(p, 1.0, 1.0, 0.88).tolist() == [0.0]


def test_arbitrage_one_cycle_per_market_day() -> None:
    # 22:00 and 23:00 CET on 1 Jan, then 00:00 and 01:00 on 2 Jan: two days, one cycle each.
    p = pd.Series([30.0, 60.0, 10.0, 40.0], index=hours("2026-01-01T22:00", 4))
    out = revenue.arbitrage(p, 1.0, 1.0, 0.88)
    assert out.index.tolist() == [pd.Timestamp("2026-01-01"), pd.Timestamp("2026-01-02")]
    assert out.tolist() == pytest.approx([0.88 * 60 - 30, 0.88 * 40 - 10])  # 22.8, 25.2


def test_arbitrage_on_quarter_hours_uses_one_hour_blocks() -> None:
    # 15-minute prices: a 1 MW / 1 MWh battery charges for 4 quarters, then sells for 4.
    # Charge block 10,10,20,20 (avg 15), sell block 50,60,70,80 (avg 65): 0.88 x 65 - 15 = 42.2.
    p = pd.Series([10.0, 10.0, 20.0, 20.0, 50.0, 60.0, 70.0, 80.0], index=quarters("2026-01-01", 8))
    assert revenue.arbitrage(p, 1.0, 1.0, 0.88).tolist() == pytest.approx([42.2])


def test_arbitrage_blocks_never_overlap() -> None:
    # A single cheap quarter (0) next to a single dear one (100) can't be used: each leg needs a
    # full hour. Best non-overlapping blocks: buy 0,40,40,40 (avg 30), sell 100,40,40,40 (avg 55)
    # -> 0.88 x 55 - 30 = 18.4.
    p = pd.Series([0.0, 40.0, 40.0, 40.0, 100.0, 40.0, 40.0, 40.0], index=quarters("2026-01-01", 8))
    assert revenue.arbitrage(p, 1.0, 1.0, 0.88).tolist() == pytest.approx([18.4])


def test_arbitrage_two_hour_battery_uses_two_hour_blocks() -> None:
    # 1 MW / 2 MWh, hourly: buy 2 MWh over hours 1-2 (avg 15), sell 1.76 MWh over hours 3-4
    # (avg 75): 2 x 0.88 x 75 - 2 x 15 = 102.
    p = pd.Series([10.0, 20.0, 70.0, 80.0], index=hours("2026-01-01", 4))
    assert revenue.arbitrage(p, 1.0, 2.0, 0.88).tolist() == pytest.approx([102.0])


def test_daily_drops_partial_day_and_keeps_dst_day(caplog: pytest.LogCaptureFixture) -> None:
    # Spring DST in CET: 2026-03-29 has 23 local hours (22:00 UTC 28 Mar to 22:00 UTC 29 Mar).
    idx = pd.date_range("2026-03-28T20:00", "2026-03-29T21:00", freq="h", tz="UTC").tz_convert(CET)
    hourly = pd.DataFrame({"a": 1.0}, index=idx)
    with caplog.at_level("INFO"):
        days = revenue.daily(hourly)
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


def test_trade_room_keeps_fcr_d_energy_reserve() -> None:
    # 1 MWh battery, 0.833 MW FCR-D: 20 min x 0.833 MW = 0.278 MWh kept each way -> 0.444 left.
    assert revenue.trade_room_mwh(1.0, 1.0, 1 / 1.2) == pytest.approx(1 - 2 * (1 / 1.2) / 3)
    assert revenue.trade_room_mwh(0.5, 1.0, 1.0) == 0.0  # never negative
    # 2 MWh leaves 1.444 MWh of room, but one hour at 1 MW can only move 1 MWh.
    assert revenue.trade_room_mwh(2.0, 1.0, 1 / 1.2) == 1.0


def test_fcr_d_plus_trade_swaps_two_hours_when_it_pays() -> None:
    # FCR-D pays 10 EUR every hour. Trade 0.5 MWh at 80%: buy at 20 (hour 0), sell at 200
    # (hour 3): 0.8 x 0.5 x 200 - 0.5 x 20 = 70, minus 2 x 10 FCR-D given up = +50.
    idx = hours("2026-01-01", 4)
    fcr = pd.Series([10.0] * 4, index=idx)
    da = pd.Series([20.0, 50.0, 50.0, 200.0], index=idx)
    assert revenue.fcr_d_plus_trade(fcr, da, 0.5, 0.8).tolist() == pytest.approx([40 + 50])


def test_fcr_d_plus_trade_stays_in_fcr_when_trade_does_not_pay() -> None:
    idx = hours("2026-01-01", 4)
    fcr = pd.Series([100.0] * 4, index=idx)  # FCR-D income beats any trade
    da = pd.Series([20.0, 50.0, 50.0, 200.0], index=idx)
    assert revenue.fcr_d_plus_trade(fcr, da, 0.5, 0.8).tolist() == [400.0]


def test_fcr_d_plus_trade_charges_before_selling() -> None:
    # The cheap hour comes after the dear one, so no legal trade: FCR-D only.
    idx = hours("2026-01-01", 2)
    fcr = pd.Series([1.0, 1.0], index=idx)
    da = pd.Series([200.0, 20.0], index=idx)
    assert revenue.fcr_d_plus_trade(fcr, da, 0.5, 0.8).tolist() == [2.0]


def test_hourly_mean_handles_the_repeated_autumn_hour() -> None:
    # 2026-10-25: 00:00 and 01:00 UTC are both 02:00 local; they must stay two hours.
    idx = pd.date_range("2026-10-25T00:00", periods=8, freq="15min", tz="UTC").tz_convert(CET)
    out = revenue.hourly_mean(pd.Series([1.0] * 4 + [3.0] * 4, index=idx))
    assert out.tolist() == [1.0, 3.0]
    assert str(pd.DatetimeIndex(out.index).tz) == CET


def test_quarterly_sums_and_counts_days() -> None:
    # 31 Mar is Q1, 1-2 Apr are Q2: Q1 = 10 EUR over 1 day, Q2 = 20 + 30 over 2 days, on 2 MW.
    idx = pd.to_datetime(["2026-03-31", "2026-04-01", "2026-04-02"])
    days = pd.DataFrame({"s": [10.0, 20.0, 30.0], "label": ["x", "y", "z"]}, index=idx)
    q = revenue.quarterly(days, power_mw=2.0)
    assert q.index.astype(str).tolist() == ["2026Q1", "2026Q2"]
    assert q["s"].tolist() == [5.0, 25.0]
    assert q["days"].tolist() == [1, 2]  # partial quarters stay visible


def test_monthly_and_annualised() -> None:
    idx = pd.to_datetime(["2026-01-30", "2026-01-31", "2026-02-01"])
    days = pd.DataFrame({"s": [10.0, 20.0, 30.0], "label": ["x", "y", "z"]}, index=idx)
    m = revenue.monthly(days, power_mw=2.0)
    assert m["s"].tolist() == [15.0, 15.0]  # (10+20)/2, 30/2; text columns ignored
    # 60 EUR over 3 days on 2 MW -> 60 / 3 * 365 / 2 = 3650
    assert revenue.annualised(days, power_mw=2.0)["s"] == pytest.approx(3650.0)

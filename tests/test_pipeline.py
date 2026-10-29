from datetime import date

import pandas as pd

from nordic_bess import config, pipeline

PARAMS = config.Params(
    start=date(2023, 1, 1),
    end=date(2023, 1, 2),
    trend_start=date(2023, 1, 1),
    power_mw=1.0,
    energy_mwh=1.0,
    round_trip_efficiency=0.88,
    availability=1.0,
    durations_h=(1.0, 2.0),
)


def test_duration_sensitivity_scales_bids_by_stored_energy() -> None:
    # Two days at a flat 10 EUR/MW/h for every FCR product, no day-ahead prices.
    hours = pd.date_range("2023-01-01", periods=48, freq="h", tz=config.MARKET_TZ)
    prices = pd.concat(
        pd.DataFrame({"timestamp_cet": hours, "product": p, "price": 10.0})
        for p in ("fcr_n", "fcr_d_up", "fcr_d_down")
    )
    out = pipeline.duration_sensitivity(prices, PARAMS, with_day_ahead=False)

    # 1 h: FCR-N bid is energy-capped at 0.5 MW -> 10 x 24 x 0.5 x 365 = 43,800 a year.
    # 2 h: the cap moves to power, 1 / 1.34 MW -> 65,373. FCR-D stays power-capped at 1 / 1.2.
    assert out["1h"]["fcr_n"] == 43_800
    assert out["2h"]["fcr_n"] == round(10 * 24 * 365 / 1.34)
    assert out["1h"]["fcr_d_stacked"] == out["2h"]["fcr_d_stacked"] == round(20 * 24 * 365 / 1.2)
    # Best-of here is FCR-D (it pays more); per MWh it halves when the energy doubles.
    assert out["2h"]["best_of_per_mwh"] == round(20 * 24 * 365 / 1.2 / 2)

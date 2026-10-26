import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from nordic_bess import config, report, revenue, validate

PARAMS = config.Params(
    start=date(2023, 1, 1),
    end=date(2023, 12, 31),
    trend_start=date(2022, 1, 1),
    power_mw=1.0,
    energy_mwh=1.0,
    round_trip_efficiency=0.88,
    availability=1.0,
)


def two_years() -> pd.DataFrame:
    """Daily EUR: 2022 pays 10 (FCR-N) and 20 (FCR-D) a day, 2023 pays 5 and 2."""
    idx = pd.date_range("2022-01-01", "2023-12-31", freq="D")
    in_2022 = idx.year == 2022
    days = pd.DataFrame(
        {"fcr_n": np.where(in_2022, 10.0, 5.0), "fcr_d_stacked": np.where(in_2022, 20.0, 2.0)},
        index=idx,
    )
    return revenue.best_of(days)


def test_summary_headline_and_trend_are_hand_checkable() -> None:
    s = report.build_summary(two_years(), PARAMS, {"fcr_n": []})

    # 2023: 5 EUR x 365 days for FCR-N, which wins every day, so best-of equals it.
    assert s["annualised_eur_per_mw_year"] == {"fcr_n": 1825, "fcr_d_stacked": 730, "best_of": 1825}
    # Peak full year is 2022 with best-of 20 x 365 = 7300; 1825 / 7300 - 1 = -75 %.
    assert s["trend"]["peak_full_year"] == "2022"
    assert s["trend"]["headline_vs_peak_pct"] == -75.0
    # Flat daily revenue: the 31-day January beats the 28-day February.
    assert s["best_month"]["fcr_n"] == {"month": "2023-01", "eur_per_mw": 155}
    assert s["worst_month"]["fcr_n"] == {"month": "2023-02", "eur_per_mw": 140}
    # Oct-Dec (92 days) vs Jan-Mar (90 days) at the same daily rate: +2.2 %.
    assert s["change_first_3_to_last_3_months_pct"]["fcr_n"] == pytest.approx(2.2)
    # Bids for 1 MW / 1 MWh, and what nameplate 1 MW would have paid: 1825 / 0.5 = 3650.
    assert s["bids_mw"] == {"fcr_n": 0.5, "fcr_d_up_and_down_each": 0.833}
    assert s["fcr_d_plus_trade_mwh"] == 0.444  # 1 - 2 x (0.833 MW x 20 min)
    assert s["nameplate_bid_sensitivity_eur_per_mw_year"]["fcr_n"] == 3650
    assert s["data_quality"] == ["all checks passed"]


def test_quarterly_section_and_first_fcr_n_lead() -> None:
    s = report.build_summary(two_years(), PARAMS, {"fcr_n": []})
    q = s["quarterly_eur_per_mw"]
    # 2022 Q1: 90 days x 10 (FCR-N) and x 20 (FCR-D); 2023 Q1: 90 days x 5 and x 2.
    assert q["2022Q1"] == {"fcr_n": 900, "fcr_d_stacked": 1800, "best_of": 1800, "days": 90}
    assert q["2023Q1"]["fcr_n"] == 450 and q["2023Q1"]["fcr_d_stacked"] == 180
    # FCR-N first beats FCR-D in 2023 Q1, when it pays 5 a day against 2.
    assert s["first_quarter_fcr_n_out_earned_fcr_d"] == "2023Q1"


def test_summary_lists_failed_checks_only() -> None:
    quality = {
        "fcr_n": [
            validate.CheckResult("no_duplicates", True, 0, "none"),
            validate.CheckResult("outliers_flagged", False, 9, "threshold 100"),
        ]
    }
    s = report.build_summary(two_years(), PARAMS, quality)
    assert s["data_quality"] == [
        {"product": "fcr_n", "check": "outliers_flagged", "rows": 9, "detail": "threshold 100"}
    ]


def test_write_outputs_creates_every_file(tmp_path: Path) -> None:
    hours = pd.date_range("2023-01-01", "2023-12-31 23:00", freq="h", tz=config.MARKET_TZ)
    rng = np.random.default_rng(0)
    prices = pd.concat(
        pd.DataFrame(
            {"timestamp_cet": hours, "product": p, "price": rng.uniform(1, 20, len(hours))}
        )
        for p in ("fcr_n", "fcr_d_up", "fcr_d_down")
    )

    summary = report.write_outputs(two_years(), prices, PARAMS, {}, out_dir=tmp_path)

    csv = pd.read_csv(tmp_path / "monthly_revenue.csv")
    assert list(csv.columns) == ["month_cet", "fcr_n", "fcr_d_stacked", "best_of"]
    assert len(csv) == 24
    assert json.loads((tmp_path / "summary.json").read_text()) == summary
    charts = ("monthly_revenue.png", "quarterly_revenue.png", "price_distribution.png")
    for name in (*charts, "daily_price_profile.png"):
        assert (tmp_path / "charts" / name).stat().st_size > 10_000

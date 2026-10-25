"""Paths, API settings, market rules and the loader for user inputs in params.toml."""

import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

# Repo root resolved from this file, so paths work from any working directory (rule 8).
ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT / ".env"
PARAMS_FILE = ROOT / "params.toml"
DATA_RAW = ROOT / "data" / "raw"
DATA_CLEAN = ROOT / "data" / "clean"
DATA_OUTPUT = ROOT / "data" / "output"

# FCR bids are made per market day in CET/CEST (dataset pages: "for each CET-timezone day"),
# so daily decisions and monthly totals use this calendar. Everything else stays in UTC.
MARKET_TZ = "Europe/Berlin"


@dataclass(frozen=True)
class Params:
    """User inputs: analysis periods and battery assumptions (all dates inclusive)."""

    start: date
    end: date
    trend_start: date
    power_mw: float
    energy_mwh: float
    round_trip_efficiency: float
    availability: float


def load_params(path: Path = PARAMS_FILE) -> Params:
    """Read and validate params.toml; fail with a clear message instead of computing nonsense."""
    with path.open("rb") as f:
        raw = tomllib.load(f)
    # Exact key sets catch typos like "power_mv", which would otherwise fall back silently.
    expected = {
        "period": {"start", "end", "trend_start"},
        "battery": {"power_mw", "energy_mwh", "round_trip_efficiency", "availability"},
    }
    for section, keys in expected.items():
        got = set(raw.get(section, {}))
        if got != keys:
            raise ValueError(f"{path.name} [{section}]: missing {keys - got}, unknown {got - keys}")
    period, battery = raw["period"], raw["battery"]
    for key in expected["period"]:
        # A quoted "2025-10-01" parses as text and a time-of-day makes a datetime; both would
        # break date comparisons later, so only a bare TOML date is accepted.
        if type(period[key]) is not date:
            raise ValueError(f"{path.name} [period] {key}: use a bare date like 2025-10-01")
    params = Params(
        start=period["start"],
        end=period["end"],
        trend_start=period["trend_start"],
        power_mw=float(battery["power_mw"]),
        energy_mwh=float(battery["energy_mwh"]),
        round_trip_efficiency=float(battery["round_trip_efficiency"]),
        availability=float(battery["availability"]),
    )
    if not params.trend_start <= params.start <= params.end:
        raise ValueError(f"{path.name}: need trend_start <= start <= end")
    if params.power_mw <= 0 or params.energy_mwh <= 0:
        raise ValueError(f"{path.name}: power_mw and energy_mwh must be positive")
    if not (0 < params.round_trip_efficiency <= 1 and 0 < params.availability <= 1):
        raise ValueError(f"{path.name}: round_trip_efficiency and availability must be in (0, 1]")
    return params


# Market rules, not choices: reservations a battery (limited energy reservoir, LER) must keep per
# MW of FCR offered. Table 10 of the Nordic FCR Technical Requirements v1.1, valid in Finland
# from 14.5.2025 (Fingrid notes it is re-evaluated regularly; FCR-N energy may move to 1-1.5 h):
# https://www.fingrid.fi/globalassets/dokumentit/fi/sahkomarkkinat/reservit/technical-requirements-for-frequency-containment-reserve-provision-in-the-nordic-synchronous-area_14.5.2025.pdf
FCR_N_POWER_PER_MW = 1.34  # MW of power kept free in each direction per MW of FCR-N
FCR_N_ENERGY_H = 1.0  # hours of full activation kept in *each* direction
FCR_D_OPPOSITE_POWER_SHARE = 0.20  # extra power kept in the opposite direction per MW of FCR-D
FCR_D_ENERGY_H = 20 / 60  # hours of full activation kept in the product's own direction

# Fingrid open data API. Source: https://data.fingrid.fi/en/instructions and the OpenAPI spec at
# https://developer-data.fingrid.fi/api-details#api=avoindata-api&operation=GetDatasetData
FINGRID_BASE_URL = "https://data.fingrid.fi/api"
FINGRID_PAGE_SIZE = 20_000  # API maximum; one month of hourly data fits in a single page
FINGRID_MIN_INTERVAL_S = 2.0  # documented limit: one request every two seconds per key

# Validation: values above this percentile are flagged for review (never dropped). 0.999 of
# 8,760 hours flags about 9 hours per product per year.
OUTLIER_QUANTILE = 0.999

# All three FCR prices share one unit (dataset pages say "EUR/MW", priced per hour).
FCR_UNIT = "EUR/MW/h"

# Why these three: Frequency Containment Reserves (FCR) are the Finnish capacity markets where
# grid-scale batteries earn most of their money. Fingrid pays for MW kept *available* to react
# to frequency within seconds, not for MWh delivered, which suits a fast, small-energy battery.
# Each price is EUR per MW per hour, set by the most expensive accepted bid in Fingrid's hourly
# market, and published the evening before (22:45 EET). Definitions per the dataset pages
# https://data.fingrid.fi/en/datasets/{317,318,283}:
#   FCR-N (317): normal-operation reserve; holds frequency in the 49.9-50.1 Hz band, so the
#     unit must both raise and lower output continuously (symmetric, used every hour).
#   FCR-D up (318): disturbance reserve; adds power when frequency falls, keeping it above
#     49.5 Hz after a large plant or interconnector trips. Activated rarely, paid every hour.
#   FCR-D down (283): disturbance reserve; absorbs power (battery charges) when frequency
#     rises, keeping it below 50.5 Hz after a large load is lost. Data starts 21.4.2021.
FINGRID_DATASETS: dict[int, str] = {
    317: "fcr_n",
    318: "fcr_d_up",
    283: "fcr_d_down",
}

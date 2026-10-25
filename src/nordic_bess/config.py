"""Dataset IDs, period, battery assumptions and paths: every tunable value lives here."""

from datetime import date
from pathlib import Path

# Repo root resolved from this file, so paths work from any working directory (rule 8).
ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT / ".env"
DATA_RAW = ROOT / "data" / "raw"
DATA_CLEAN = ROOT / "data" / "clean"
DATA_OUTPUT = ROOT / "data" / "output"

# Headline period: the last 12 full calendar months before the build date (2026-10-25).
# Inclusive on both ends.
PERIOD_START = date(2025, 10, 1)
PERIOD_END = date(2026, 9, 30)
# Trend period: four full Oct-Sep years, to show how revenue changed as batteries saturated FCR.
TREND_START = date(2022, 10, 1)

# FCR bids are made per market day in CET/CEST (dataset pages: "for each CET-timezone day"),
# so daily decisions and monthly totals use this calendar. Everything else stays in UTC.
MARKET_TZ = "Europe/Berlin"

# Battery assumptions from the brief.
POWER_MW = 1.0
ENERGY_MWH = 1.0
ROUND_TRIP_EFFICIENCY = 0.88
AVAILABILITY = 1.0

# Largest compliant FCR bids for a battery (limited energy reservoir), from Table 10 of the
# Nordic FCR Technical Requirements v1.1, valid in Finland from 14.5.2025:
# https://www.fingrid.fi/globalassets/dokumentit/fi/sahkomarkkinat/reservit/technical-requirements-for-frequency-containment-reserve-provision-in-the-nordic-synchronous-area_14.5.2025.pdf
# Per MW of FCR-N: 1.34 MW of power and 1 h of energy reserved in *each* direction.
FCR_N_BID_MW = min(POWER_MW / 1.34, ENERGY_MWH / 2.0)  # 1 MW / 1 MWh -> 0.5 MW (energy-bound)
# Per MW of FCR-D up + down stacked (allowed; columns are summed, p. 40): 1 + 0.2 MW of power
# and 20 min of energy reserved in each direction.
FCR_D_BID_MW = min(POWER_MW / 1.2, ENERGY_MWH / (2 / 3))  # -> 0.833 MW each way (power-bound)

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

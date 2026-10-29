# A battery in Finland earned up to 61,273 EUR/MW in the last 12 months, 82% below the 2023 peak

[![CI](https://github.com/Tarun1297/nordic-bess-revenue/actions/workflows/ci.yml/badge.svg)](https://github.com/Tarun1297/nordic-bess-revenue/actions/workflows/ci.yml)

**Question:** where does a 1 MW / 1 MWh battery in Finland earn its money, and how has that changed?

**Answer (October 2025 – September 2026):** mostly in **FCR-D**, topped up by one daily trade. Offering FCR-D up and down together every hour earned **53,215 EUR/MW/year**. Giving up two hours a day to buy cheap and sell dear raised that to **58,454**, the best single strategy. FCR-N earned **39,831** and pure day-ahead arbitrage **21,038**. Picking the best of these strategies every day with perfect hindsight gives **61,273**, a ceiling for these strategies only. Since the 2023 peak of **347,833**, that figure has fallen by 82%, as batteries have filled Finland's reserve markets.

## Key findings

- **FCR-D, with one trade on top, is the main revenue stream for a 1-hour battery.** Each day's best strategy: FCR-D + one trade on 224 of 365 days, FCR-D alone on 77, FCR-N on 62 and pure arbitrage on 2.
- **Fingrid's battery rules decide the ranking.** A 1 MWh battery may bid only **0.5 MW** into FCR-N but **0.833 MW** into each FCR-D direction. If the full 1 MW could be bid, FCR-N would lead with 79,663 against FCR-D's 63,858. A 2-hour battery can bid more FCR-N, and the ranking flips back. To check, set `energy_mwh = 2.0` in [params.toml](params.toml) and re-run.
- **Reserve revenue is collapsing; trading revenue is not.** Annualised by calendar year, in EUR per MW:

  | Year | FCR-N | FCR-D up + down | Day-ahead arbitrage | FCR-D + one trade | Daily best-of |
  |---|---|---|---|---|---|
  | 2022 | 156,943 | 273,032 | 59,328 | 288,005 | 315,185 |
  | 2023 | 206,299 | 319,876 | 20,923 | 322,472 | 347,833 |
  | 2024 | 200,676 | 247,274 | 23,816 | 251,600 | 290,891 |
  | 2025 | 80,071 | 163,188 | 25,116 | 168,226 | 171,665 |
  | 2026 (Jan–Sep) | 40,080 | 47,882 | 20,927 | 53,218 | 56,911 |

  FCR-D fell by about 85% from 2023 to 2026, while arbitrage has held between 20,923 and 25,116 a year since 2023, after the 2022 energy crisis. In the last 12 months arbitrage earned 40% as much as FCR-D. 2026 covers January to September only; for the same months in every year, see `ytd_like_for_like` in [summary.json](data/output/summary.json).
- **Trading hours matter more as reserve prices fall.** Adding one trade to FCR-D was worth under 2% a year in 2023 and 2024, but 11% in 2026 and 10% over the last 12 months.
- **The decline is still running, and it hits FCR-D hardest.** From the first quarter of the headline year (Oct–Dec 2025) to the last (Jul–Sep 2026), FCR-D revenue fell 42.5% and arbitrage 25.7%, while FCR-N rose 5.6%. The best month was October 2025 (8,960 EUR/MW, best-of) and the worst was December 2025 (3,170).
- **Quarter by quarter, FCR-N has now caught up.** In Q3 2026 FCR-N earned 10,402 EUR/MW against FCR-D's 10,007: the first quarter it came out ahead. FCR-D's best quarter was Q3 2023, at 143,277. Arbitrage has stayed between 3,247 and 6,981 per quarter since 2023, so its share of the quarterly best-of rose from 3% in Q2 2023 to 33–41% in 2026. See the [quarterly chart](data/output/charts/quarterly_revenue.png) and [quarterly_revenue.csv](data/output/quarterly_revenue.csv).
- **FCR-D up is an evening product.** Its price peaks at 18:00 CET, 3.4× its 03:00 low. FCR-N and FCR-D down stay almost flat through the day.

![Monthly battery revenue, 2022 to 2026](data/output/charts/monthly_revenue.png)

More charts: [price distribution per product](data/output/charts/price_distribution.png) and [average price by hour of day](data/output/charts/daily_price_profile.png). All numbers come from [summary.json](data/output/summary.json) and [monthly_revenue.csv](data/output/monthly_revenue.csv). A test fails if this README and summary.json disagree.

## Method in plain words

1. **Download** hourly FCR prices from Fingrid's open data API (datasets 317, 318 and 283) and Finnish day-ahead prices from the ENTSO-E Transparency Platform, December 2021 to September 2026.
2. **Check** the data before using it: explicit UTC timestamps, no duplicate intervals, every day complete, no gaps. Price spikes are flagged but never removed. Every check passed. The flags are the expected ones: spikes in the top 0.1% of intervals, and day-ahead prices moving from hourly to 15-minute steps on 1 October 2025. They are listed in `data_quality` in summary.json.
3. **Tidy** the data into one table of prices in CET, the time zone the market days use. FCR prices are hourly; day-ahead prices keep their 15-minute steps.
4. **Calculate revenue** for each strategy. FCR: price × MW offered, every hour, where the MW offered is the largest bid Fingrid's rules allow for this battery. Arbitrage: the best charge-then-discharge cycle of each day. FCR-D + one trade: FCR-D in every hour but the best charge hour and sell hour (see [How a battery makes money](#how-a-battery-makes-money)).
5. **Summarise** by day, month and year, and write the CSV, the JSON and the charts.

One command rebuilds everything from an empty `data/` folder. All inputs (periods, battery size, efficiency, availability) are in [params.toml](params.toml), each with the reason for its value.

## Limits

These numbers show where the money **is** in the market, not what a real project would earn. A real battery earns less, because:

- **No perfect foresight.** Bids are placed before prices are known, so a real operator won't always pick the better product, or the best trading hours. The trading figures and the daily best-of assume perfect hindsight.
- **Only these schedules.** The daily best-of is the best of the modelled strategies, not of every possible schedule. Splitting the battery's power between FCR and trading, or more than one trade a day, is not modelled.
- **100% availability.** Maintenance and outages are not modelled. Set `availability` in params.toml for a scenario.
- **No bidding strategy.** The model assumes every bid is accepted at the clearing price. A real bid can be rejected, or set the price itself.
- **No degradation, fees or tariffs.** Battery wear, grid fees and market fees are not deducted.
- **Capacity payments only for FCR.** Payments for energy actually delivered during activations are not included.
- **Hourly FCR market and day-ahead only.** Fingrid's yearly FCR market, aFRR, mFRR and intraday trading are not modelled.
- **Today's rules applied to earlier years.** The bid limits come from the May 2025 requirements and are used for 2022–2025 as well, so the years compare like for like. The rules were different then.

## What this means for new Nordic projects

*Author's section, to be written.*

## How a battery makes money

A grid battery has two ways to earn in Finland: standing ready for the grid (frequency reserves) and trading energy (arbitrage).

### Standing ready for the grid (frequency reserves)

The grid has to stay at 50 Hz. When more power is used than produced, frequency falls. When more is produced than used, it rises. Fingrid, the Finnish grid operator, buys **Frequency Containment Reserves (FCR)**: capacity that reacts automatically within seconds to push the frequency back.

Fingrid pays for **being available**, not for energy delivered. The owner promises "for this hour, X MW of my battery will respond to frequency" and is paid the hourly price for that promise, whether or not the grid calls on it.

| Product | What the battery does | When it activates |
|---|---|---|
| **FCR-N** (normal operation) | Charges or discharges a little, continuously | Whenever frequency leaves 50.0 Hz; full output at 49.9 or 50.1 Hz |
| **FCR-D up** (disturbance) | Discharges | Below 49.9 Hz; full output at 49.5 Hz, e.g. when a large plant trips |
| **FCR-D down** (disturbance) | Charges | Above 50.1 Hz; full output at 50.5 Hz, e.g. when a large load drops out |

```
revenue (EUR) = price (EUR/MW/h) × MW offered × 1 hour
```

**Fingrid's rules limit how much a battery can offer.** For every MW offered, a battery must keep spare power and spare stored energy, so it can react and still manage its charge level ([Nordic FCR Technical Requirements](https://www.fingrid.fi/globalassets/dokumentit/fi/sahkomarkkinat/reservit/technical-requirements-for-frequency-containment-reserve-provision-in-the-nordic-synchronous-area_14.5.2025.pdf), Table 10, valid in Finland from 14 May 2025):

- **FCR-N:** 1 hour of energy in *each* direction per MW offered. A 1 MWh battery at half charge can therefore back only **0.5 MW**.
- **FCR-D up + down at the same time:** this is allowed ([Batteries in the Nordic reserve markets](https://www.fingrid.fi/globalassets/dokumentit/fi/sahkomarkkinat/reservit/batteries-in-the-nordic-reserve-markets-april-2025.pdf), p. 7). Each direction needs its own MW plus 20% in the other direction, so 1.2 × 0.833 = 1 MW of power: **0.833 MW** each way.
- **The same MW can't be sold in two markets at once,** so the strategies below are alternatives.

### Trading energy (day-ahead arbitrage)

The battery buys electricity when it is cheap and sells it when it is expensive, once per day. It charges at full power for one hour, buying 1 MWh, and later sells the 0.88 MWh it gets back (88% round-trip efficiency) over another hour. The charging hour must end before the selling hour starts. Since 1 October 2025 day-ahead prices come in 15-minute steps, so each "hour" is any run of four consecutive quarter-hours, priced at their average.

```
revenue (EUR) = 0.88 MWh × average sell price − 1 MWh × average buy price
```

*Example:* buy at 20 EUR/MWh and sell at 120 EUR/MWh gives 0.88 × 120 − 20 = 85.60 EUR for the day. If the spread is too small to cover the losses, the battery doesn't trade.

### Strategies compared

1. **FCR-N only:** 0.5 MW of FCR-N every hour.
2. **FCR-D up + down:** 0.833 MW of each, every hour.
3. **Day-ahead arbitrage:** one charge and one discharge per day, at the best prices of that day.
4. **FCR-D + one trade:** FCR-D in every hour except one charge hour and one later sell hour. The trade is sized so FCR-D keeps its Table 10 energy reserve in every other hour: a 1 MWh battery offering 0.833 MW of FCR-D must hold 20 minutes of it (0.278 MWh) in each direction, which leaves 1 − 2 × 0.278 = 0.444 MWh to trade. The battery trades only on days when the trade earns more than the FCR-D income it gives up in those two hours.
5. **Daily best-of:** the best of the above for each day, with perfect hindsight. It is a ceiling for these strategies, not for every possible schedule.

### Market context

Independent sources describe the same shift. [Olana](https://olana.fi/blog-posts/the-merchant-bess-business-case) (May 2026) says reserve revenues have peaked and energy-market revenues are rising. [Solarplaza](https://www.solarplaza.com/resource/13522/article-finlands-bess-market-beyond-the-gold-rush-2026/) (July 2026) reports about 1.5 GW of batteries built in Finland in a couple of years, and calls the reserve market "quite saturated". [RaboResearch](https://www.rabobank.com/knowledge/d011535313-backup-power-for-europe-part-8-ranking-bess-market-attractivity-in-the-nordics) (July 2026) sees value moving from FCR and FFR to the deeper aFRR and mFRR markets, and reports Finnish day-ahead spreads averaging above 90 EUR/MWh.

### Cross-check against public sources

Our side of each comparison is in `public_benchmarks` in [summary.json](data/output/summary.json). Published figures are written as "k", so they are not confused with ours.

| What | Ours | Published | Verdict |
|---|---|---|---|
| Day-ahead mean, 2023 (EUR/MWh) | 56.5 | 56.5 ([Finnish Energy](https://energia.fi/wp-content/uploads/2024/01/Electricity-price-statistics-2023.pdf)) | Match |
| Day-ahead mean, 2024 | 45.6 | 45.8 ([euenergy.live](https://euenergy.live/finland/2024)), 46.1 ([Electricity Maps](https://www.electricitymaps.com/grid-in-review-2025/finland)) | Match |
| Day-ahead mean, 2025 | 42.1 | 40.8 ([Electricity Maps](https://www.electricitymaps.com/grid-in-review-2025/finland)) | Close: they average daily prices, and Q4 2025 used 15-minute prices |
| Day-ahead mean daily spread, last 12 months | 89.6 | "above 90" ([RaboResearch](https://www.rabobank.com/knowledge/d011535313-backup-power-for-europe-part-8-ranking-bess-market-attractivity-in-the-nordics)) | Match |
| FCR-N mean price, 2024 (EUR/MW/h) | 45.8 | 46 ([Clean Horizon](https://www.cleanhorizon.com/news/finland-price-forecast-s1-2025-updated/)) | Match |
| 1-hour battery, Sep 2023, scaled to a year (EUR/MW/year) | 632,182 | 645k ([Clean Horizon Storage Index](https://www.cleanhorizon.com/news/storage-index-update-finland-in-focus/)) | Within 2% |
| 1-hour battery, Aug 2026, scaled to a year | 43,584 | 50k (Clean Horizon Storage Index) | Ours lower, as expected: aFRR, mFRR and intraday are not modelled |

Two differences are not resolved:

- Clean Horizon names September 2023 as the peak. Our highest month is October 2022 (680,199 EUR/MW/year scaled). Their index may start later than our data; we have not confirmed this.
- [Gridcog](https://www.gridcog.com/blog/nordic-power-markets-battery-storage-opportunity) reports FCR-D averaging below 3 EUR/MW/h by February 2025. Fingrid's own hourly prices for that month average well above that in both directions. We use Fingrid's data and have not found what Gridcog measured.

## How to run

Requires Windows, macOS or Linux, [uv](https://docs.astral.sh/uv/), a free [Fingrid API key](https://developer-data.fingrid.fi/) (Products → Open Data starter → Subscribe, then Profile → Primary key) and, for arbitrage, a free [ENTSO-E token](https://transparencyplatform.zendesk.com/hc/en-us/articles/12845911031188-How-to-get-security-token). Without the ENTSO-E token the pipeline still runs and skips arbitrage.

```bash
uv venv --python 3.12 .venv
uv pip install -e ".[dev,entsoe]"
cp .env.example .env
```

Put your key after `FINGRID_API_KEY=` in `.env`, then:

```bash
.venv/Scripts/python -m nordic_bess.pipeline
```

On macOS and Linux, use `.venv/bin/python`. The first run downloads about 177 monthly files at Fingrid's limit of one request per 2 seconds, which takes about 8 minutes. Later runs reuse the files on disk and take seconds. Options:

- `--config my_scenario.toml` for different inputs
- `--start` and `--end` (YYYY-MM-DD) for a different headline period
- `--refresh` to download again

Checks: `pytest`, `ruff check .`, `ruff format --check .`, `mypy src`.

Data: Fingrid open data, [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

# Nordic BESS revenue benchmark (Finland)

Where does a 1 MW / 1 MWh battery in Finland earn its money, and how has that changed over the last 12 months?

Work in progress. The answer-first write-up lands in phase 6.

## How a battery makes money

A grid battery has two ways to earn in Finland. This project models both and compares them hour by hour.

### 1. Being on standby for the grid (frequency reserves)

The grid has to stay at 50 Hz. When more power is used than produced, frequency falls; when more is produced than used, it rises. Fingrid, the Finnish grid operator, buys **Frequency Containment Reserves (FCR)**: capacity that reacts automatically within seconds to push frequency back.

Fingrid pays for **being available**, not for energy delivered. The owner promises "for this hour, 1 MW of my battery will respond to frequency", and is paid the hourly price for that promise, whether or not the grid actually calls on it. A battery suits this well because it can switch from charging to discharging in milliseconds, and the promise uses a lot of MW but little MWh.

There are three products, each with its own hourly price in EUR per MW per hour:

| Product | What the battery does | When it's needed |
|---|---|---|
| **FCR-N** (normal operation) | Continuously charges or discharges a little to keep frequency between 49.9 and 50.1 Hz | All the time, in both directions |
| **FCR-D up** (disturbance, upward) | Discharges to keep frequency above 49.5 Hz | Rarely: after a large power plant or interconnector trips |
| **FCR-D down** (disturbance, downward) | Charges to keep frequency below 50.5 Hz | Rarely: after a large load or export link drops out |

Each hourly price is set by the most expensive bid Fingrid accepts for that hour, and it's published the evening before. Revenue for one hour is simple:

```
revenue (EUR) = price (EUR/MW/h) × capacity offered (MW) × 1 hour
```

*Example:* if FCR-N clears at 12 EUR/MW/h, a 1 MW offer earns 12 EUR for that hour. Over a year, that is summed across 8,760 hours.

### 2. Buying low, selling high (day-ahead arbitrage)

The second way is energy trading on the day-ahead market, where each hour of tomorrow has its own electricity price in EUR/MWh. The battery charges when power is cheap, often at night or on windy hours, sometimes even at negative prices, and discharges when power is expensive, usually in the evening peak.

Some energy is lost in each cycle. With a round-trip efficiency of 88%, the battery has to buy about 1.14 MWh to sell 1 MWh:

```
revenue (EUR) = sell price × 1 MWh − buy price × (1 MWh ÷ 0.88)
```

*Example:* buy at 20 EUR/MWh, sell at 120 EUR/MWh → 120 − 20 ÷ 0.88 = **97.27 EUR** for the day. If the spread is too small to cover the losses, the battery doesn't trade that day.

The model allows one cycle per day, and the charge hour must come before the discharge hour. This part runs once day-ahead data is added (phase 8).

### 3. Choosing between them

The same 1 MW can't be sold twice. If it's promised to FCR-N for an hour, it can't also be used to trade energy in that hour. So the project compares strategies:

1. **FCR-N only:** offer 1 MW to FCR-N every hour.
2. **FCR-D up + down:** offer 1 MW to both FCR-D products at once. A battery can respond in both directions, because discharging covers *up* and charging covers *down*. *Assumption, to be confirmed against Fingrid's market rules.*
3. **Day-ahead arbitrage:** one charge/discharge cycle per day.
4. **Daily best-of:** for each day, pick whichever strategy earned most. This is an **upper bound**, because it assumes the battery knew all prices in advance.

### What the benchmark leaves out

These numbers show where the money *is* in the market, not what a real project would bank. A real battery earns less, because:

- **No perfect foresight.** Bids are placed before prices are known, so a real operator won't always pick the best product.
- **Not available 100% of the time.** Maintenance and outages take hours away.
- **Energy limits.** A 1 MWh battery can't hold 1 MW of reserve indefinitely in one direction. Fingrid's technical requirements make battery owners keep charge headroom, so the bid is usually below nameplate.
- **Wear, fees and activation energy.** Degradation, grid and market fees, and settlement of energy actually delivered during activations are not modelled.
- **Hourly-market prices.** Fingrid also procures FCR through a yearly market. This project uses the hourly-market price only.

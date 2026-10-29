"""Gold outputs: monthly_revenue.csv, summary.json and three charts in data/output/."""

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")  # headless: renders to files on any machine, including CI
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

from nordic_bess import config, revenue, validate  # noqa: E402

log = logging.getLogger(__name__)

# The trading strategies appear only when day-ahead prices were loaded (ENTSO-E key set).
ALL_STRATEGIES = ["fcr_n", "fcr_d_stacked", "arbitrage", "fcr_d_plus_trade", "best_of"]


def strategies(days: pd.DataFrame) -> list[str]:
    """Strategy columns present in the daily table, in a fixed reporting order."""
    return [s for s in ALL_STRATEGIES if s in days.columns]


LABELS = {
    "fcr_n": "FCR-N",
    "fcr_d_stacked": "FCR-D up + down",
    "arbitrage": "Day-ahead arbitrage",
    "fcr_d_plus_trade": "FCR-D + one trade",
    "best_of": "Daily best-of",
    "fcr_d_up": "FCR-D up",
    "fcr_d_down": "FCR-D down",
}
# Colour follows the entity in every chart: FCR-N is always blue, the FCR-D family orange,
# FCR-D down aqua, arbitrage violet, FCR-D + one trade green. Validated for colour-blind
# separation (dataviz validate_palette.js, light).
COLORS = {
    "arbitrage": "#6250d6",
    "fcr_d_plus_trade": "#008300",
    "fcr_n": "#2a78d6",
    "fcr_d_stacked": "#eb6834",
    "fcr_d_up": "#eb6834",
    "fcr_d_down": "#1baf7a",
}
SURFACE, INK, INK_2, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9"

SOURCES = [
    {
        "name": "ENTSO-E Transparency Platform: day-ahead prices, Finland (article 12.1.D)",
        "url": "https://transparency.entsoe.eu/",
    },
    {
        "name": "ENTSO-E Restful API integration guide",
        "url": "https://transparencyplatform.zendesk.com/hc/en-us/articles/15692855254548-Sitemap-for-Restful-API-Integration",
    },
    {
        "name": "Fingrid open data: FCR-N hourly market prices (dataset 317)",
        "url": "https://data.fingrid.fi/en/datasets/317",
    },
    {
        "name": "Fingrid open data: FCR-D up hourly market prices (dataset 318)",
        "url": "https://data.fingrid.fi/en/datasets/318",
    },
    {
        "name": "Fingrid open data: FCR-D down hourly market prices (dataset 283)",
        "url": "https://data.fingrid.fi/en/datasets/283",
    },
    {
        "name": "Nordic FCR Technical Requirements v1.1 (Table 10: battery reservations)",
        "url": "https://www.fingrid.fi/globalassets/dokumentit/fi/sahkomarkkinat/reservit/technical-requirements-for-frequency-containment-reserve-provision-in-the-nordic-synchronous-area_14.5.2025.pdf",
    },
    {
        "name": "Nordic TSOs: Batteries in the Nordic reserve markets (April 2025)",
        "url": "https://www.fingrid.fi/globalassets/dokumentit/fi/sahkomarkkinat/reservit/batteries-in-the-nordic-reserve-markets-april-2025.pdf",
    },
]
SOURCE_NOTE = (
    "Source: Fingrid open data (FCR prices, datasets 317, 318, 283); ENTSO-E (day-ahead prices). "
    "Bids follow Fingrid Table 10 for a {power:g} MW / {energy:g} MWh battery."
)


def _eur(x: float) -> int:
    """Round EUR to whole euros for reporting; sub-euro precision is noise at this scale."""
    return int(round(x))


def monthly_table(days: pd.DataFrame, power_mw: float) -> pd.DataFrame:
    """Monthly EUR/MW per strategy with a YYYY-MM month column, ready for CSV."""
    m = revenue.monthly(days[strategies(days)], power_mw).round(0).astype(int)
    m.index = m.index.astype(str)
    return m.rename_axis("month_cet").reset_index()


def quarterly_table(days: pd.DataFrame, power_mw: float) -> pd.DataFrame:
    """Quarterly EUR/MW per strategy with a YYYYQn quarter column and its day count, for CSV."""
    q = revenue.quarterly(days[strategies(days)], power_mw).round(0).astype(int)
    q.index = q.index.astype(str)
    return q.rename_axis("quarter_cet").reset_index()


def first_quarter_fcr_n_led(quarters: pd.DataFrame) -> str | None:
    """First quarter in which FCR-N out-earned stacked FCR-D, or None if it never did."""
    led = quarters[quarters["fcr_n"] > quarters["fcr_d_stacked"]]
    return None if led.empty else str(led["quarter_cet"].iloc[0])


def public_benchmarks(
    days: pd.DataFrame, prices: pd.DataFrame, params: config.Params
) -> dict[str, Any]:
    """Our side of the figures that public sources also publish, for the README cross-check."""
    ts = prices["timestamp_cet"]
    trend = prices[ts.dt.date >= params.trend_start]
    means = trend.groupby([trend["timestamp_cet"].dt.year, "product"])["price"].mean().round(1)
    da = prices[(prices["product"] == config.DAY_AHEAD) & (ts.dt.date >= params.start)]
    da = da[da["timestamp_cet"].dt.date <= params.end]
    daily_range = da.groupby(da["timestamp_cet"].dt.date)["price"].agg(["max", "min"])
    # Indices such as Clean Horizon's report each month scaled to a year; do the same here.
    best = revenue.monthly(days[["best_of"]], params.power_mw)["best_of"]
    n_days = pd.Series(1, index=days.index).groupby(pd.DatetimeIndex(days.index).to_period("M"))
    return {
        "mean_price_by_year": {str(y): g.droplevel(0).to_dict() for y, g in means.groupby(level=0)},
        # None without an ENTSO-E key: no day-ahead prices, so no spread (NaN is not valid JSON).
        "day_ahead_mean_daily_spread_eur_per_mwh": None
        if daily_range.empty
        else round(float((daily_range["max"] - daily_range["min"]).mean()), 1),
        "best_of_month_annualised_eur_per_mw_year": {
            str(m): _eur(v) for m, v in (best / n_days.sum() * 365).items()
        },
    }


def _ytd_mask(index: pd.DatetimeIndex, end: date) -> Any:
    """Days whose (month, day) is on or before end's, in any year: a like-for-like YTD window."""
    return (index.month < end.month) | ((index.month == end.month) & (index.day <= end.day))


def build_summary(
    days: pd.DataFrame,
    params: config.Params,
    quality: dict[str, list[validate.CheckResult]],
) -> dict[str, Any]:
    """Assemble summary.json: headline answer, trend, data quality, assumptions and sources."""
    fcr_n_mw, fcr_d_mw = revenue.max_fcr_bids(params.power_mw, params.energy_mwh)
    headline = days.loc[str(params.start) : str(params.end)]
    annual = revenue.annualised(headline[strategies(days)], params.power_mw)
    months = revenue.monthly(headline[strategies(days)], params.power_mw)
    # Change from the first three to the last three months of the headline period.
    first3, last3 = months.iloc[:3].sum(), months.iloc[-3:].sum()

    idx = pd.DatetimeIndex(days.index)
    yearly = {
        str(year): {
            k: _eur(v) for k, v in revenue.annualised(g[strategies(days)], params.power_mw).items()
        }
        | {"days": len(g)}
        for year, g in days.groupby(idx.year)
    }
    ytd = days[_ytd_mask(idx, params.end)]
    ytd_yearly = {
        str(year): {
            k: _eur(v) for k, v in revenue.annualised(g[strategies(days)], params.power_mw).items()
        }
        for year, g in ytd.groupby(pd.DatetimeIndex(ytd.index).year)
    }
    full_years = {y: v for y, v in yearly.items() if v["days"] >= 365}
    peak_year = max(full_years, key=lambda y: full_years[y]["best_of"])

    return {
        "question": "Where does a battery in Finland earn its money, and how has that changed?",
        "headline_period": {
            "start": str(params.start),
            "end": str(params.end),
            "timezone": config.MARKET_TZ,
        },
        "battery": {
            "power_mw": params.power_mw,
            "energy_mwh": params.energy_mwh,
            "round_trip_efficiency": params.round_trip_efficiency,
            "availability": params.availability,
        },
        "bids_mw": {"fcr_n": round(fcr_n_mw, 3), "fcr_d_up_and_down_each": round(fcr_d_mw, 3)},
        # Energy the FCR-D + one trade strategy can trade while keeping FCR-D's energy reserve.
        "fcr_d_plus_trade_mwh": round(
            revenue.trade_room_mwh(params.energy_mwh, params.power_mw, fcr_d_mw), 3
        ),
        "annualised_eur_per_mw_year": {k: _eur(v) for k, v in annual.items()},
        # What the same prices would pay if the full nameplate MW could be bid (rules ignored).
        "nameplate_bid_sensitivity_eur_per_mw_year": {
            "fcr_n": _eur(annual["fcr_n"] / fcr_n_mw * params.power_mw),
            "fcr_d_stacked": _eur(annual["fcr_d_stacked"] / fcr_d_mw * params.power_mw),
        },
        "best_month": {
            s: {"month": str(months[s].idxmax()), "eur_per_mw": _eur(months[s].max())}
            for s in strategies(days)
        },
        "worst_month": {
            s: {"month": str(months[s].idxmin()), "eur_per_mw": _eur(months[s].min())}
            for s in strategies(days)
        },
        "change_first_3_to_last_3_months_pct": {
            s: round((last3[s] / first3[s] - 1) * 100, 1) for s in strategies(days)
        },
        "days_won_by_strategy": headline["best_strategy"].value_counts().to_dict(),
        # Whole trend window; "days" below a full quarter marks a partial one at the edges.
        "quarterly_eur_per_mw": {
            row.pop("quarter_cet"): row
            for row in quarterly_table(days, params.power_mw).to_dict("records")
        },
        "first_quarter_fcr_n_out_earned_fcr_d": first_quarter_fcr_n_led(
            quarterly_table(days, params.power_mw)
        ),
        "trend": {
            "calendar_year_annualised_eur_per_mw_year": yearly,
            "ytd_like_for_like_annualised_eur_per_mw_year": ytd_yearly,
            "ytd_window": f"1 Jan to {params.end:%d %b} of each year",
            "peak_full_year": peak_year,
            "headline_vs_peak_pct": round(
                (annual["best_of"] / full_years[peak_year]["best_of"] - 1) * 100, 1
            ),
        },
        "data_quality": [
            {"product": product, "check": r.name, "rows": r.rows_affected, "detail": r.detail}
            for product, results in quality.items()
            for r in results
            if not r.passed
        ]
        or ["all checks passed"],
        "assumptions": [
            "Hourly-market FCR prices only; Fingrid's yearly-market contracts are not modelled.",
            "Bids are the largest allowed by Table 10 for this battery, applied to every year.",
            "Capacity payments only; activated-energy settlement is not modelled.",
            "Perfect foresight for the daily best-of: it takes the best modelled strategy each "
            "day, so it is a ceiling for these strategies only, not for every schedule; "
            "no bidding risk.",
            f"Availability {params.availability:.0%}; no degradation, fees or grid tariffs.",
            f"Day-ahead arbitrage: one cycle a day, buying {params.energy_mwh:g} MWh in a "
            f"{params.energy_mwh / params.power_mw:g}-hour block and selling "
            f"{params.energy_mwh * params.round_trip_efficiency:g} MWh in a later block "
            "(15-minute prices from 1 Oct 2025, hourly before).",
        ],
        "sources": SOURCES,
    }


def _style(ax: Any, title: str, subtitle: str) -> None:
    """Apply the shared chart chrome: recessive axes, hairline grid, title states the finding."""
    ax.set_facecolor(SURFACE)
    ax.figure.set_facecolor(SURFACE)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(MUTED)
    ax.tick_params(colors=INK_2, length=0, labelsize=9)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.figure.text(0.06, 0.95, title, fontsize=14, fontweight="bold", color=INK, ha="left")
    ax.figure.text(0.06, 0.905, subtitle, fontsize=10, color=INK_2, ha="left")


def _source(fig: Any, text: str) -> None:
    """Put the source note at the bottom-left, in muted ink."""
    fig.text(0.06, 0.02, text, fontsize=8, color=MUTED, ha="left")


def _legend_top(ax: Any) -> None:
    """Legend in one row between subtitle and plot, where it can never cover data."""
    ax.legend(
        loc="lower left",
        bbox_to_anchor=(0, 1.01),
        ncols=len(ax.get_legend_handles_labels()[1]),  # one row, never over the subtitle
        frameon=False,
        fontsize=9,
        labelcolor=INK_2,
    )


def _end_labels(ax: Any, x: float, ends: dict[str, float]) -> None:
    """Direct labels at the line ends, nudged apart so converging lines never overlap."""
    gap = (ax.get_ylim()[1] - ax.get_ylim()[0]) * 0.045
    placed: list[float] = []
    for name, y in sorted(ends.items(), key=lambda kv: kv[1]):
        y_text = max(y, placed[-1] + gap) if placed else y
        placed.append(y_text)
        ax.annotate(
            LABELS[name],
            (x, y),
            xytext=(x, y_text),
            textcoords="data",
            va="center",
            fontsize=9,
            color=INK_2,
            annotation_clip=False,
        )


def chart_monthly(
    days: pd.DataFrame, summary: dict[str, Any], params: config.Params, path: Path
) -> None:
    """Lines of monthly revenue per strategy across the whole trend window."""
    m = revenue.monthly(days[strategies(days)], params.power_mw)
    periods = pd.PeriodIndex(m.index)
    # Months plotted by position: evenly spaced, and no date-axis conversion needed.
    x = list(range(len(m)))
    fig, ax = plt.subplots(figsize=(10, 5.5))
    fig.subplots_adjust(left=0.08, right=0.82, top=0.82, bottom=0.14)
    trend = summary["trend"]
    _style(
        ax,
        f"Battery revenue is down {-trend['headline_vs_peak_pct']:.0f}% "
        f"from its {trend['peak_full_year']} peak",
        f"Monthly revenue, EUR per MW, {params.power_mw:g} MW / {params.energy_mwh:g} MWh battery, "
        "market time (CET)",
    )
    # Shade the headline window so the 12 months behind the summary numbers are visible.
    first = list(periods).index(pd.Period(params.start, "M"))
    last = list(periods).index(pd.Period(params.end, "M"))
    ax.axvspan(first - 0.5, last + 0.5, color=GRID, alpha=0.45, lw=0)
    for s in strategies(days):
        best = s == "best_of"
        ax.plot(
            x,
            m[s],
            color=INK_2 if best else COLORS[s],
            lw=1.5 if best else 2,
            ls="--" if best else "-",
            label=LABELS[s],
        )
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}"))
    jan = [i for i, per in enumerate(periods) if per.month == 1]
    ax.set_xticks(jan, [str(periods[i].year) for i in jan])
    ax.set_ylim(bottom=0)
    ax.text(
        first - 0.5,
        ax.get_ylim()[1] * 0.97,
        " last 12 months",
        fontsize=8,
        color=INK_2,
        va="top",
    )
    # Direct labels at the line ends, so identity never rests on colour alone.
    _end_labels(ax, x[-1] + 1, {k: float(m[k].iloc[-1]) for k in strategies(days)})
    _legend_top(ax)
    _source(
        fig,
        SOURCE_NOTE.format(power=params.power_mw, energy=params.energy_mwh)
        + "\nBest-of takes the best modelled strategy each day with perfect foresight; "
        "the % change compares the best-of line.",
    )
    fig.savefig(path, dpi=150)
    plt.close(fig)


def chart_price_box(prices: pd.DataFrame, path: Path) -> None:
    """Box plot of hourly prices per product over the headline period."""
    products = ["fcr_n", "fcr_d_up", "fcr_d_down"]
    data = [prices.loc[prices["product"] == p, "price"].to_numpy() for p in products]
    med = {p: float(pd.Series(d).median()) for p, d in zip(products, data, strict=True)}
    combined = med["fcr_d_up"] + med["fcr_d_down"]
    if med["fcr_n"] > combined:
        title = "Per MW, FCR-N pays more than FCR-D up and down combined"
    else:
        title = "Per MW, FCR-D up and down combined pay more than FCR-N"
    fig, ax = plt.subplots(figsize=(10, 5.5))
    fig.subplots_adjust(left=0.08, right=0.96, top=0.85, bottom=0.14)
    _style(
        ax,
        title,
        f"Hourly prices, last 12 months, EUR/MW/h. Medians: FCR-N {med['fcr_n']:.2f}, "
        f"FCR-D up {med['fcr_d_up']:.2f}, FCR-D down {med['fcr_d_down']:.2f}",
    )
    box = ax.boxplot(data, patch_artist=True, showfliers=False, whis=1.5, widths=0.45)
    for patch, p in zip(box["boxes"], products, strict=True):
        patch.set(facecolor=COLORS[p], alpha=0.35, edgecolor=COLORS[p], linewidth=1.5)
    for part in ("whiskers", "caps"):
        for line in box[part]:
            line.set(color=INK_2, linewidth=1)
    for line in box["medians"]:
        line.set(color=INK, linewidth=2)
    ax.set_xticks([1, 2, 3], [LABELS[p] for p in products])
    ax.set_ylim(bottom=0)
    # Count the hours the whiskers leave out, so the note states exactly what is not drawn.
    hidden = 0
    for d in data:
        q1, q3 = pd.Series(d).quantile([0.25, 0.75])
        hidden += int((d > q3 + 1.5 * (q3 - q1)).sum())
    _source(
        fig,
        f"Boxes: middle 50% of hours; whiskers: 1.5 x IQR. {hidden:,} spike hours above the "
        "whiskers are not drawn (kept in the data). Source: Fingrid open data (317, 318, 283).",
    )
    fig.savefig(path, dpi=150)
    plt.close(fig)


def chart_quarterly(
    days: pd.DataFrame, summary: dict[str, Any], params: config.Params, path: Path
) -> None:
    """Draw grouped bars of quarterly revenue per strategy across the whole trend window."""
    q = quarterly_table(days, params.power_mw)
    order = ("fcr_d_plus_trade", "fcr_d_stacked", "fcr_n", "arbitrage")
    bars = [s for s in order if s in q.columns]
    periods = pd.PeriodIndex(q["quarter_cet"], freq="Q")
    first_led = summary["first_quarter_fcr_n_out_earned_fcr_d"]
    if first_led and first_led == q["quarter_cet"].iloc[-1]:
        title = f"In {first_led[-2:]} {first_led[:4]}, FCR-N out-earned FCR-D for the first time"
    elif first_led:
        title = f"FCR-N first out-earned FCR-D in {first_led[-2:]} {first_led[:4]}"
    else:
        span = f"{q['quarter_cet'].iloc[0]} to {q['quarter_cet'].iloc[-1]}"
        title = f"Quarterly revenue by strategy, {span}"
    fig, ax = plt.subplots(figsize=(10, 5.5))
    fig.subplots_adjust(left=0.08, right=0.97, top=0.82, bottom=0.17)
    _style(ax, title, "Revenue per quarter, EUR per MW, by strategy, market time (CET)")
    width = 0.8 / len(bars)
    for i, s in enumerate(bars):
        x = [j + (i - (len(bars) - 1) / 2) * width for j in range(len(q))]
        ax.bar(x, q[s], width=width * 0.9, color=COLORS[s], label=LABELS[s])
    # A quarter is full at 90+ days; anything shorter is a partial quarter at the edges.
    for j, n in enumerate(q["days"]):
        if n < 90:
            ax.text(j, 0, "partial", rotation=90, fontsize=7, color=MUTED, ha="center", va="bottom")
    # Year under each Q1 (and the first bar), so the axis reads as a calendar without clutter.
    labels = [
        f"Q{per.quarter}\n{per.year}" if per.quarter == 1 or j == 0 else f"Q{per.quarter}"
        for j, per in enumerate(periods)
    ]
    ax.set_xticks(range(len(q)), labels)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.set_xlim(-0.6, len(q) - 0.4)
    _legend_top(ax)
    _source(fig, SOURCE_NOTE.format(power=params.power_mw, energy=params.energy_mwh))
    fig.savefig(path, dpi=150)
    plt.close(fig)


def chart_daily_profile(prices: pd.DataFrame, path: Path) -> None:
    """Average price by hour of the CET day, per product, over the headline period."""
    prof = (
        prices.assign(hour=prices["timestamp_cet"].dt.hour)
        .groupby(["hour", "product"])["price"]
        .mean()
        .unstack()
    )
    # Title names the product whose price swings most through the day.
    swing = str((prof.max() / prof.min()).idxmax())
    hours = prof.index.to_numpy()
    peak_h, low_h = int(hours[prof[swing].argmax()]), int(hours[prof[swing].argmin()])
    ratio = prof[swing].max() / prof[swing].min()
    fig, ax = plt.subplots(figsize=(10, 5.5))
    fig.subplots_adjust(left=0.08, right=0.86, top=0.82, bottom=0.12)
    _style(
        ax,
        f"{LABELS[swing]} prices peak at {peak_h:02d}:00 CET, "
        f"{ratio:.1f}x their {low_h:02d}:00 low",
        "Average hourly price by hour of day, last 12 months, EUR/MW/h, market time (CET)",
    )
    for p in ["fcr_n", "fcr_d_up", "fcr_d_down"]:
        ax.plot(prof.index, prof[p], color=COLORS[p], lw=2, label=LABELS[p])
    ax.set_xticks(range(0, 24, 3), [f"{h:02d}:00" for h in range(0, 24, 3)])
    ax.set_xlim(0, 23)
    ax.set_ylim(bottom=0)
    _end_labels(ax, 23.4, {p: float(prof[p].iloc[-1]) for p in ["fcr_n", "fcr_d_up", "fcr_d_down"]})
    _legend_top(ax)
    _source(fig, "Source: Fingrid open data (datasets 317, 318, 283), hourly-market FCR prices.")
    fig.savefig(path, dpi=150)
    plt.close(fig)


def write_outputs(
    days: pd.DataFrame,
    prices: pd.DataFrame,
    params: config.Params,
    quality: dict[str, list[validate.CheckResult]],
    durations: dict[str, dict[str, int]] | None = None,
    out_dir: Path = config.DATA_OUTPUT,
) -> dict[str, Any]:
    """Write the monthly and quarterly CSVs, summary.json and the charts; return the summary."""
    charts = out_dir / "charts"
    charts.mkdir(parents=True, exist_ok=True)
    monthly_table(days, params.power_mw).to_csv(out_dir / "monthly_revenue.csv", index=False)
    quarterly_table(days, params.power_mw).to_csv(out_dir / "quarterly_revenue.csv", index=False)
    summary = build_summary(days, params, quality) | {
        "public_benchmarks": public_benchmarks(days, prices, params),
        # Same headline period and power, different hours of energy (params [sensitivity]).
        "duration_sensitivity_eur_per_mw_year": durations or {},
    }
    # Stable key order and no generation timestamp, so re-runs on the same inputs diff clean.
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    ts = prices["timestamp_cet"]
    # Price charts are about the FCR products; day-ahead prices only feed arbitrage revenue.
    fcr = prices["product"].isin(list(config.FINGRID_DATASETS.values()))
    headline_prices = prices[fcr & (ts.dt.date >= params.start) & (ts.dt.date <= params.end)]
    chart_monthly(days, summary, params, charts / "monthly_revenue.png")
    chart_quarterly(days, summary, params, charts / "quarterly_revenue.png")
    chart_price_box(headline_prices, charts / "price_distribution.png")
    chart_daily_profile(headline_prices, charts / "daily_price_profile.png")
    log.info("wrote monthly and quarterly CSVs, summary.json and 4 charts to %s", out_dir)
    return summary

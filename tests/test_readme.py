import json
import re
from typing import Any

from nordic_bess import config

README = (config.ROOT / "README.md").read_text(encoding="utf-8")
SUMMARY = json.loads((config.DATA_OUTPUT / "summary.json").read_text(encoding="utf-8"))


def _numbers(node: Any) -> set[str]:
    """Every number in summary.json, formatted the way the README writes it."""
    if isinstance(node, dict):
        return set().union(*(_numbers(v) for v in node.values())) if node else set()
    if isinstance(node, list):
        return set().union(*(_numbers(v) for v in node)) if node else set()
    if isinstance(node, bool) or not isinstance(node, int | float):
        return set()
    if isinstance(node, int) or node.is_integer():
        return {f"{int(node):,}"}
    return {f"{node:g}", f"{abs(node):g}"}


def test_readme_euro_figures_all_come_from_summary() -> None:
    # Definition of done: README numbers match summary.json exactly. Every thousands-separated
    # figure in the README (the EUR amounts) must exist in summary.json, so a stale README fails.
    # Published figures from other sources are written as "645k", so they are not caught here.
    in_readme = set(re.findall(r"\b\d{1,3}(?:,\d{3})+\b", README))
    assert in_readme, "README has no EUR figures to check"
    assert in_readme <= _numbers(SUMMARY), f"not in summary.json: {in_readme - _numbers(SUMMARY)}"


def test_readme_percentages_match_summary() -> None:
    trend = SUMMARY["trend"]
    change = SUMMARY["change_first_3_to_last_3_months_pct"]
    assert f"{-trend['headline_vs_peak_pct']:.0f}%" in README  # "84%" in the headline
    assert f"{abs(change['fcr_d_stacked']):g}%" in README
    assert f"{change['fcr_n']:g}%" in README
    assert f"**{SUMMARY['bids_mw']['fcr_n']:g} MW**" in README
    assert f"**{SUMMARY['bids_mw']['fcr_d_up_and_down_each']:g} MW**" in README


def test_readme_cross_check_uses_summary_values() -> None:
    # Our column of the cross-check table must be the pipeline's own numbers.
    pb = SUMMARY["public_benchmarks"]
    years = pb["mean_price_by_year"]
    for cell in [
        years["2023"]["day_ahead"],
        years["2024"]["day_ahead"],
        years["2025"]["day_ahead"],
        years["2024"]["fcr_n"],
        pb["day_ahead_mean_daily_spread_eur_per_mwh"],
    ]:
        assert f"| {cell:g} |" in README

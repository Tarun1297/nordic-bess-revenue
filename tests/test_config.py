from datetime import date
from pathlib import Path

import pytest

from nordic_bess import config

VALID = """
[period]
start = 2025-10-01
end = 2026-09-30
trend_start = 2022-10-01

[battery]
power_mw = 2
energy_mwh = 4.0
round_trip_efficiency = 0.9
availability = 0.97

[sensitivity]
durations_h = [1, 2.5]
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "params.toml"
    path.write_text(text)
    return path


def test_repo_params_file_loads() -> None:
    # The committed params.toml must always be valid: every published number comes from it.
    assert config.load_params().power_mw > 0


def test_valid_file_parses_dates_and_floats(tmp_path: Path) -> None:
    p = config.load_params(write(tmp_path, VALID))
    assert p.start == date(2025, 10, 1) and p.trend_start == date(2022, 10, 1)
    assert p.power_mw == 2.0 and isinstance(p.power_mw, float)
    assert p.durations_h == (1.0, 2.5)


def test_typo_in_key_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown {'power_mv'}"):
        config.load_params(write(tmp_path, VALID.replace("power_mw", "power_mv")))


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("start = 2025-10-01", "start = 2026-10-01"),  # start after end
        ("start = 2025-10-01", 'start = "2025-10-01"'),  # quoted date is text, not a date
        ("trend_start = 2022-10-01", "trend_start = 2026-01-01"),  # trend after headline start
        ("energy_mwh = 4.0", "energy_mwh = 0"),
        ("availability = 0.97", "availability = 1.2"),
        ("round_trip_efficiency = 0.9", "round_trip_efficiency = 0"),
        ("durations_h = [1, 2.5]", "durations_h = [1, 0]"),
    ],
)
def test_invalid_values_are_rejected(tmp_path: Path, old: str, new: str) -> None:
    with pytest.raises(ValueError):
        config.load_params(write(tmp_path, VALID.replace(old, new)))

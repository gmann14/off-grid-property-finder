"""Tests for the shared threshold-band lookup."""

import math

import pytest

from src.constants import (
    BUILDABLE_PERCENT_THRESHOLDS,
    ELEVATION_THRESHOLDS,
    HYDRO_POWER_THRESHOLDS,
    OPEN_GROUND_PERCENT_THRESHOLDS,
    PARCEL_SIZE_THRESHOLDS,
    SOLAR_PERCENT_THRESHOLDS,
    WIND_SPEED_THRESHOLDS,
)
from src.scoring.bands import band_score

OPEN_ENDED_TABLES = [
    HYDRO_POWER_THRESHOLDS,
    PARCEL_SIZE_THRESHOLDS,
    WIND_SPEED_THRESHOLDS,
    OPEN_GROUND_PERCENT_THRESHOLDS,
    BUILDABLE_PERCENT_THRESHOLDS,
    SOLAR_PERCENT_THRESHOLDS,
    ELEVATION_THRESHOLDS,
]


def test_bands_are_half_open():
    table = [(10, math.inf, 100), (5, 10, 50), (0, 5, 10)]
    assert band_score(10, table) == 100   # low edge is in the band
    assert band_score(9.999, table) == 50
    assert band_score(5, table) == 50
    assert band_score(0, table) == 10


@pytest.mark.parametrize("table", OPEN_ENDED_TABLES)
def test_open_ended_top_band_takes_inf(table):
    top = max(table, key=lambda band: band[0])
    assert top[1] == math.inf
    assert band_score(math.inf, table) == top[2]


def test_no_match_nan_and_minus_inf_score_zero():
    table = [(10, math.inf, 100), (0, 10, 50)]
    assert band_score(-1, table) == 0
    assert band_score(math.nan, table) == 0
    assert band_score(-math.inf, table) == 0


def test_closed_top_band_does_not_take_inf():
    assert band_score(math.inf, [(0, 10, 50)]) == 0


def test_first_match_wins_on_overlap():
    assert band_score(5, [(0, 10, 1), (5, 10, 2)]) == 1

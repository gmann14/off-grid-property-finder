"""Tests for solar scoring."""

import pytest

from src.scoring.solar import _classify_solar_pixel
from src.constants import SOLAR_FLAT_SLOPE


def test_flat_terrain_is_optimal():
    # Below SOLAR_FLAT_SLOPE should be classified as optimal (2)
    assert _classify_solar_pixel(0, 2) == 2
    assert _classify_solar_pixel(45, 3) == 2
    assert _classify_solar_pixel(270, SOLAR_FLAT_SLOPE - 1) == 2


def test_south_facing_moderate_slope_optimal():
    # Aspect 180 (due south), slope 20 degrees
    assert _classify_solar_pixel(180, 20) == 2


def test_east_facing_acceptable():
    # Aspect 100 (east-ish), slope 15
    assert _classify_solar_pixel(100, 15) == 1


def test_north_facing_poor():
    # Aspect 0 (due north), slope 20 — outside acceptable range
    assert _classify_solar_pixel(0, 20) == 0
    assert _classify_solar_pixel(350, 25) == 0


def test_steep_north_facing_poor():
    assert _classify_solar_pixel(10, 40) == 0

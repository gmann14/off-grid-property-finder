"""Tests for elevation scoring."""

import pandas as pd
import pytest

from src.scoring.elevation import _lookup_score, score_elevation
from src.constants import ELEVATION_THRESHOLDS


def test_lookup_score_in_range():
    # Redesign: elevation is now a coastal-flood penalty only.
    assert _lookup_score(50, ELEVATION_THRESHOLDS) == 100   # safe above surge
    assert _lookup_score(15, ELEVATION_THRESHOLDS) == 70    # marginal low coastal
    assert _lookup_score(7, ELEVATION_THRESHOLDS) == 40     # low coastal, real risk
    assert _lookup_score(3, ELEVATION_THRESHOLDS) == 10     # tidal/surge zone


def test_high_ground_no_longer_penalized():
    # The whole point of the redesign: high, exposed ground (good for wind)
    # must score full marks, not be penalized like the old table did.
    assert _lookup_score(150, ELEVATION_THRESHOLDS) == 100
    assert _lookup_score(300, ELEVATION_THRESHOLDS) == 100
    assert _lookup_score(20, ELEVATION_THRESHOLDS) == 100   # boundary: safe start
    assert _lookup_score(-1, ELEVATION_THRESHOLDS) == 0     # below sea level


def test_score_elevation_with_fixture(small_grid, config_with_paths):
    """Test elevation scoring against synthetic DEM."""
    scores = score_elevation(small_grid, config_with_paths)
    assert isinstance(scores, pd.Series)
    assert len(scores) == len(small_grid)
    # All scores should be valid (0-100)
    assert (scores >= 0).all()
    assert (scores <= 100).all()


def test_score_elevation_no_dem(small_grid, config_with_paths):
    """When DEM is missing, all scores should be 0."""
    import os
    dem_path = config_with_paths.paths.processed / "dem.tif"
    if dem_path.exists():
        os.remove(dem_path)
    scores = score_elevation(small_grid, config_with_paths)
    assert (scores == 0).all()

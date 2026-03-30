"""Tests for elevation scoring."""

import pandas as pd
import pytest

from src.scoring.elevation import _lookup_score, score_elevation
from src.constants import ELEVATION_THRESHOLDS


def test_lookup_score_in_range():
    assert _lookup_score(50, ELEVATION_THRESHOLDS) == 100   # 30-100 sweet spot
    assert _lookup_score(150, ELEVATION_THRESHOLDS) == 90   # 100-200 band
    assert _lookup_score(5, ELEVATION_THRESHOLDS) == 10     # 0-10 coastal floodplain


def test_lookup_score_boundaries():
    assert _lookup_score(100, ELEVATION_THRESHOLDS) == 90   # exactly at 100 → 100-200 band
    assert _lookup_score(300, ELEVATION_THRESHOLDS) == 30   # 300+ band
    assert _lookup_score(30, ELEVATION_THRESHOLDS) == 100   # sweet spot start


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

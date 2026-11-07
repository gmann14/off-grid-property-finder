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


def _write_dem(path, arr):
    import rasterio
    from rasterio.transform import from_bounds

    h, w = arr.shape
    with rasterio.open(path, "w", driver="GTiff", height=h, width=w, count=1, dtype="float32",
                       crs="EPSG:2961", transform=from_bounds(380000, 4900000, 381000, 4901000, w, h),
                       nodata=-32767) as d:
        d.write(arr.astype("float32"), 1)


def _dem_cfg(tmp_path):
    from src.config import Config, Paths, StudyArea

    return Config(study_area=StudyArea(bbox=(380000, 4900000, 381000, 4901000)),
                  paths=Paths(raw=tmp_path / "raw", processed=tmp_path, output=tmp_path / "out"))


def test_shore_cell_is_scored_on_its_land_not_the_sea(tmp_path, small_grid):
    """Regression: the mean took in the sea pixels (DEM <= 0) of a shore cell,
    so a cell whose land averages 8 m (score 40) averaged 4.4 m and scored 10
    (78 eligible real cells changed band)."""
    import numpy as np

    dem = np.full((100, 100), 8.0)
    dem[:, :10] = -1.0     # the west 40% of each west-column cell is sea
    dem[:, 25:50] = -0.5   # the second column of cells is all below sea level
    _write_dem(tmp_path / "dem.tif", dem)
    scores = score_elevation(small_grid, _dem_cfg(tmp_path))
    west = small_grid.geometry.bounds["minx"] == 380000
    second = small_grid.geometry.bounds["minx"] == 380250
    assert (scores[west] == 40).all()      # land mean 8 m, not 0.6*8 - 0.4 = 4.4 m
    assert (scores[second] == 0).all()     # no land: whole-cell mean, not the no-DEM 50
    assert (scores[~west & ~second] == 40).all()


def test_score_elevation_no_dem(small_grid, config_with_paths):
    """When DEM is missing, all scores should be 0."""
    import os
    dem_path = config_with_paths.paths.processed / "dem.tif"
    if dem_path.exists():
        os.remove(dem_path)
    scores = score_elevation(small_grid, config_with_paths)
    assert (scores == 0).all()

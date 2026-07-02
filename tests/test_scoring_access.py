"""Tests for access scoring."""

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import LineString, box

from src.scoring.access import _compute_min_distances, _lookup_score
from src.constants import ACCESS_DISTANCE_THRESHOLDS

CRS = "EPSG:2961"


def test_distance_zero_max_score():
    assert _lookup_score(0, ACCESS_DISTANCE_THRESHOLDS) == 100


def test_distance_25m():
    assert _lookup_score(25, ACCESS_DISTANCE_THRESHOLDS) == 80


def test_distance_100m():
    assert _lookup_score(100, ACCESS_DISTANCE_THRESHOLDS) == 50


def test_distance_300m():
    assert _lookup_score(300, ACCESS_DISTANCE_THRESHOLDS) == 20


def test_distance_1000m():
    assert _lookup_score(1000, ACCESS_DISTANCE_THRESHOLDS) == 0


# --- _compute_min_distances (bulk STRtree rewrite; previously untested) ----

def test_min_distances_cell_polygon_intersects_feature():
    """A feature crossing the FAR CORNER of a cell (not near the centroid)
    must still score 0 — direct polygon intersection, not centroid distance."""
    cell = box(0, 0, 250, 250)  # centroid at (125, 125)
    road = LineString([(240, 240), (300, 300)])  # crosses only the far corner
    candidates = gpd.GeoDataFrame(geometry=[cell], crs=CRS)
    features = gpd.GeoDataFrame(geometry=[road], crs=CRS)

    result = _compute_min_distances(candidates, features)
    assert result.iloc[0] == 0.0


def test_min_distances_within_max_dist():
    cell = box(1000, 1000, 1250, 1250)  # centroid (1125, 1125)
    road = LineString([(1125, 1400), (1300, 1400)])
    candidates = gpd.GeoDataFrame(geometry=[cell], crs=CRS)
    features = gpd.GeoDataFrame(geometry=[road], crs=CRS)

    result = _compute_min_distances(candidates, features, max_dist=500.0)
    assert result.iloc[0] == pytest.approx(275.0, abs=0.01)


def test_min_distances_beyond_max_dist_is_inf():
    cell = box(0, 0, 250, 250)
    road = LineString([(10000, 10000), (10100, 10100)])
    candidates = gpd.GeoDataFrame(geometry=[cell], crs=CRS)
    features = gpd.GeoDataFrame(geometry=[road], crs=CRS)

    result = _compute_min_distances(candidates, features, max_dist=500.0)
    assert np.isinf(result.iloc[0])


def test_min_distances_empty_features_all_inf():
    candidates = gpd.GeoDataFrame(geometry=[box(0, 0, 250, 250), box(300, 300, 550, 550)], crs=CRS)
    features = gpd.GeoDataFrame(geometry=[], crs=CRS)

    result = _compute_min_distances(candidates, features)
    assert np.isinf(result).all()
    assert len(result) == 2


def test_min_distances_mixed_cells_independent():
    """Three cells with three different outcomes computed in ONE bulk call —
    verifies the vectorized rewrite doesn't cross-contaminate results between
    cells (a real risk when replacing a per-cell loop with array ops)."""
    cells = [
        box(0, 0, 250, 250),          # intersects road A -> 0
        box(1000, 1000, 1250, 1250),  # near road B, within max_dist
        box(9000, 9000, 9250, 9250),  # far from everything -> inf
    ]
    roads = [
        LineString([(200, 200), (300, 300)]),      # crosses cell 0's corner
        LineString([(1125, 1400), (1300, 1400)]),  # 275m from cell 1's centroid
    ]
    candidates = gpd.GeoDataFrame(geometry=cells, crs=CRS)
    features = gpd.GeoDataFrame(geometry=roads, crs=CRS)

    result = _compute_min_distances(candidates, features, max_dist=500.0)
    assert result.iloc[0] == 0.0
    assert result.iloc[1] == pytest.approx(275.0, abs=0.01)
    assert np.isinf(result.iloc[2])

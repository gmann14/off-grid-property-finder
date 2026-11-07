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


@pytest.mark.parametrize("distance, score", [
    (50, 80), (50.01, 50), (200, 50), (200.01, 20), (500, 20), (500.01, 0),
])
def test_within_band_edges_are_inclusive(distance, score):
    """Regression: SPEC says "within 50 m" etc., but the upper edges were
    exclusive, so a road exactly 50 m away scored 50, not 80."""
    assert _lookup_score(distance, ACCESS_DISTANCE_THRESHOLDS) == score


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
    cell = box(1000, 1000, 1250, 1250)  # top edge y=1250, centroid (1125, 1125)
    road = LineString([(1125, 1400), (1300, 1400)])
    candidates = gpd.GeoDataFrame(geometry=[cell], crs=CRS)
    features = gpd.GeoDataFrame(geometry=[road], crs=CRS)

    result = _compute_min_distances(candidates, features, max_dist=500.0)
    assert result.iloc[0] == pytest.approx(150.0, abs=0.01)  # from the cell, not its centre


def test_road_just_outside_the_cell_is_within_50m():
    """Regression: distance was measured from the cell centre, so a road just
    outside a 250 m cell was already >= 125 m away and the "within 50 m -> 80"
    band could never trigger (7,377 real cells scored under the SPEC table)."""
    cell = box(0, 0, 250, 250)
    road = LineString([(-100, 280), (400, 280)])  # 30 m above the top edge
    candidates = gpd.GeoDataFrame(geometry=[cell], crs=CRS)
    features = gpd.GeoDataFrame(geometry=[road], crs=CRS)

    d = _compute_min_distances(candidates, features).iloc[0]
    assert d == pytest.approx(30.0)
    assert _lookup_score(d, ACCESS_DISTANCE_THRESHOLDS) == 80


def test_score_access_measures_from_the_cell(tmp_path):
    """End to end: roads 150 m and 400 m beyond a cell's edge score 50 and 20
    (within 200 m / 500 m of the cell); from the centre they were 275 m and
    525 m (20 and 0), and the 50 one was flagged access_unverified."""
    from src.config import Config, Paths, StudyArea
    from src.scoring.access import score_access

    cells = gpd.GeoDataFrame(geometry=[box(0, 0, 250, 250), box(5000, 0, 5250, 250)], crs=CRS)
    gpd.GeoDataFrame(
        {"highway": ["residential", "track"]},
        geometry=[LineString([(-500, 400), (500, 400)]), LineString([(4500, 650), (5500, 650)])],
        crs=CRS,
    ).to_file(tmp_path / "roads.gpkg", driver="GPKG")
    cfg = Config(study_area=StudyArea(bbox=(380000, 4900000, 381000, 4901000)),
                 paths=Paths(raw=tmp_path / "raw", processed=tmp_path, output=tmp_path / "out"))
    assert score_access(cells, cfg).tolist() == [50.0, 20.0]



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
        LineString([(1125, 1400), (1300, 1400)]),  # 150m from cell 1's top edge
    ]
    candidates = gpd.GeoDataFrame(geometry=cells, crs=CRS)
    features = gpd.GeoDataFrame(geometry=roads, crs=CRS)

    result = _compute_min_distances(candidates, features, max_dist=500.0)
    assert result.iloc[0] == 0.0
    assert result.iloc[1] == pytest.approx(150.0, abs=0.01)
    assert np.isinf(result.iloc[2])

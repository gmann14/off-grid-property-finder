"""Tests for exclusion layer loading and application."""

import geopandas as gpd
import pytest
from shapely.geometry import box

from src.exclusions import apply_exclusions, load_exclusions

CRS = "EPSG:2961"
BBOX = (380000.0, 4900000.0, 381000.0, 4901000.0)


def test_load_exclusions_no_files():
    result = load_exclusions(None, None, CRS, BBOX)
    assert result.empty
    assert "exclusion_reason" in result.columns


def test_load_exclusions_with_protected(sample_exclusion_zones):
    result = load_exclusions(sample_exclusion_zones, None, CRS, BBOX)
    assert len(result) > 0
    assert (result["exclusion_reason"] == "protected_area").all()


def test_apply_exclusions_empty():
    grid = gpd.GeoDataFrame(
        geometry=[box(0, 0, 250, 250)],
        crs=CRS,
    )
    empty = gpd.GeoDataFrame(columns=["geometry", "exclusion_reason"], crs=CRS)
    result = apply_exclusions(grid, empty)
    assert (result["status"] == "eligible").all()


def test_apply_exclusions_centroid_containment():
    xmin, ymin = BBOX[0], BBOX[1]
    # Cell whose centroid is inside the exclusion zone
    grid = gpd.GeoDataFrame(
        geometry=[box(xmin, ymin, xmin + 250, ymin + 250)],
        crs=CRS,
    )
    exclusions = gpd.GeoDataFrame(
        {"exclusion_reason": ["protected_area"]},
        geometry=[box(xmin, ymin, xmin + 500, ymin + 500)],
        crs=CRS,
    )
    result = apply_exclusions(grid, exclusions)
    assert result["status"].iloc[0] == "excluded"
    assert "protected_area" in result["exclusion_reasons"].iloc[0]


def test_apply_exclusions_overlap_threshold():
    xmin, ymin = BBOX[0], BBOX[1]
    # Cell: 250x250, exclusion covers 60% of it
    grid = gpd.GeoDataFrame(
        geometry=[box(xmin, ymin, xmin + 250, ymin + 250)],
        crs=CRS,
    )
    # Exclusion doesn't contain centroid but overlaps >50%
    exclusions = gpd.GeoDataFrame(
        {"exclusion_reason": ["flood_zone"]},
        geometry=[box(xmin - 100, ymin - 100, xmin + 200, ymin + 200)],
        crs=CRS,
    )
    result = apply_exclusions(grid, exclusions, overlap_threshold=0.5)
    # Overlap = 200*200 = 40000, Cell = 250*250 = 62500, ratio = 0.64 > 0.5
    assert result["status"].iloc[0] == "excluded"
    assert "flood_zone" in result["exclusion_reasons"].iloc[0]


def test_apply_exclusions_overlap_below_threshold_stays_eligible():
    """Regression for the spatial-join rewrite: cells that touch an exclusion
    polygon but don't clear the overlap threshold must stay eligible — the
    sjoin pre-filter must not itself decide exclusion, only narrow candidates."""
    xmin, ymin = BBOX[0], BBOX[1]
    grid = gpd.GeoDataFrame(geometry=[box(xmin, ymin, xmin + 250, ymin + 250)], crs=CRS)
    # Small corner overlap only: 20x20 = 400 out of 62500 -> ~0.6%, well under 50%
    exclusions = gpd.GeoDataFrame(
        {"exclusion_reason": ["flood_zone"]},
        geometry=[box(xmin - 10, ymin - 10, xmin + 10, ymin + 10)],
        crs=CRS,
    )
    result = apply_exclusions(grid, exclusions, overlap_threshold=0.5)
    assert result["status"].iloc[0] == "eligible"


def test_apply_exclusions_sums_overlap_across_multiple_polygons():
    """A cell overlapping TWO separate exclusion polygons, each below threshold
    alone, must be excluded once their combined overlap clears the threshold —
    and both reasons must appear (tests the groupby-sum + reason-union path)."""
    xmin, ymin = BBOX[0], BBOX[1]
    grid = gpd.GeoDataFrame(geometry=[box(xmin, ymin, xmin + 250, ymin + 250)], crs=CRS)
    # Two horizontal strips (top + bottom) that straddle the cell but neither
    # contains its centroid (at y=125) — so this must be decided by the OVERLAP
    # path, not the centroid-containment check (which runs first and would
    # otherwise mask a bug in the overlap path).
    exclusions = gpd.GeoDataFrame(
        {"exclusion_reason": ["flood_zone", "protected_area"]},
        geometry=[
            box(xmin, ymin, xmin + 250, ymin + 70),          # bottom: 250x70=17500 (28%)
            box(xmin, ymin + 180, xmin + 250, ymin + 250),   # top: 250x70=17500 (28%)
        ],
        crs=CRS,
    )
    result = apply_exclusions(grid, exclusions, overlap_threshold=0.5)
    # Combined overlap = 35000 / 62500 = 0.56 >= 0.5
    assert result["status"].iloc[0] == "excluded"
    reasons = result["exclusion_reasons"].iloc[0]
    assert "flood_zone" in reasons and "protected_area" in reasons
    assert result["status"].iloc[0] == "excluded"

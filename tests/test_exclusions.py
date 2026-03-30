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

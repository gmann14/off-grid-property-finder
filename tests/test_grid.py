"""Tests for candidate grid generation and filtering."""

import geopandas as gpd
import pytest
from shapely.geometry import box

from src.grid import filter_by_rural_mask, generate_candidate_grid

CRS = "EPSG:2961"
BBOX = (380000.0, 4900000.0, 381000.0, 4901000.0)


def test_generate_grid_dimensions():
    grid = generate_candidate_grid(BBOX, cell_size=250, crs=CRS)
    # 1000m / 250m = 4 columns, 4 rows = 16 cells
    assert len(grid) == 16


def test_generate_grid_crs():
    grid = generate_candidate_grid(BBOX, cell_size=250, crs=CRS)
    assert str(grid.crs) == CRS


def test_grid_cells_are_squares():
    grid = generate_candidate_grid(BBOX, cell_size=250, crs=CRS)
    for _, row in grid.iterrows():
        bounds = row.geometry.bounds
        width = bounds[2] - bounds[0]
        height = bounds[3] - bounds[1]
        assert abs(width - 250) < 0.01
        assert abs(height - 250) < 0.01


def test_grid_covers_bbox():
    grid = generate_candidate_grid(BBOX, cell_size=250, crs=CRS)
    total_bounds = grid.total_bounds
    assert total_bounds[0] == pytest.approx(BBOX[0], abs=1)
    assert total_bounds[1] == pytest.approx(BBOX[1], abs=1)


def test_filter_by_rural_mask_none():
    grid = generate_candidate_grid(BBOX, cell_size=250, crs=CRS)
    filtered = filter_by_rural_mask(grid, None)
    assert len(filtered) == len(grid)


def test_filter_by_rural_mask_partial():
    grid = generate_candidate_grid(BBOX, cell_size=250, crs=CRS)
    xmin, ymin, xmax, ymax = BBOX
    # Rural mask covers only the western half
    rural = gpd.GeoDataFrame(
        geometry=[box(xmin, ymin, xmin + 500, ymax)],
        crs=CRS,
    )
    filtered = filter_by_rural_mask(grid, rural)
    assert len(filtered) < len(grid)
    assert len(filtered) > 0
    # All kept cells should have centroids in the western half
    for _, row in filtered.iterrows():
        assert row.geometry.centroid.x < xmin + 500 + 1  # small tolerance


def test_generate_grid_custom_cell_size():
    grid = generate_candidate_grid(BBOX, cell_size=500, crs=CRS)
    # 1000m / 500m = 2 columns, 2 rows = 4 cells
    assert len(grid) == 4

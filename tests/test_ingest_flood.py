"""Test the flood-raster polygonize helper (pure, no network)."""

import numpy as np
from rasterio.transform import from_bounds

from src.ingest import _mask_to_polygons

CRS = "EPSG:2961"
BBOX = (380000.0, 4900000.0, 381000.0, 4901000.0)  # 1km x 1km


def test_mask_to_polygons_area():
    # 10x10 grid over 1km -> 100m px; a 4x4 block of 1s = 400m x 400m = 160,000 m^2
    mask = np.zeros((10, 10), dtype="uint8")
    mask[2:6, 2:6] = 1
    transform = from_bounds(*BBOX, 10, 10)
    gdf = _mask_to_polygons(mask, transform, CRS)
    assert len(gdf) == 1
    assert abs(gdf.geometry.area.sum() - 160000.0) < 1.0
    assert str(gdf.crs).endswith("2961")


def test_mask_to_polygons_empty():
    mask = np.zeros((10, 10), dtype="uint8")
    transform = from_bounds(*BBOX, 10, 10)
    gdf = _mask_to_polygons(mask, transform, CRS)
    assert gdf.empty

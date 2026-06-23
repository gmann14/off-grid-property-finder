"""Tests for buildability mask — water and building exclusion (redesign fix)."""

import geopandas as gpd
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_bounds
from shapely.geometry import LineString, box

from src.mask import build_buildability_mask

CRS = "EPSG:2961"
BBOX = (380000.0, 4900000.0, 381000.0, 4901000.0)


def _write_flat_slope(path):
    """All-flat slope raster (everything passes the slope rule)."""
    w = h = 100
    transform = from_bounds(*BBOX, w, h)
    arr = np.zeros((h, w), dtype="float32")  # 0 degrees everywhere
    with rasterio.open(path, "w", driver="GTiff", height=h, width=w, count=1,
                       dtype="float32", crs=CRS, transform=transform, nodata=-9999) as d:
        d.write(arr, 1)
    return path


def _fraction_buildable(mask_path):
    with rasterio.open(mask_path) as s:
        a = s.read(1)
    return (a == 1).mean()


def test_slope_only_is_all_buildable(tmp_path):
    slope = _write_flat_slope(tmp_path / "slope.tif")
    build_buildability_mask(slope, None, None, None, BBOX, CRS)
    # No water/buildings → flat terrain is fully buildable
    assert _fraction_buildable(tmp_path / "buildability_mask.tif") == 1.0


def test_water_is_excluded(tmp_path):
    slope = _write_flat_slope(tmp_path / "slope.tif")
    xmin, ymin, xmax, ymax = BBOX
    streams = gpd.GeoDataFrame(
        {"id": [1]},
        geometry=[LineString([(xmin, (ymin + ymax) / 2), (xmax, (ymin + ymax) / 2)])],
        crs=CRS,
    )
    water_path = tmp_path / "streams.gpkg"
    streams.to_file(water_path, driver="GPKG")

    build_buildability_mask(slope, None, water_path, None, BBOX, CRS, water_buffer_m=50)
    frac = _fraction_buildable(tmp_path / "buildability_mask.tif")
    # A buffered stream across the middle must carve out a non-trivial strip
    assert frac < 1.0
    assert frac > 0.5  # but most land still buildable


def test_forest_is_excluded(tmp_path):
    slope = _write_flat_slope(tmp_path / "slope.tif")
    xmin, ymin, xmax, ymax = BBOX
    # NSTDB-style land cover: a TREE AREA polygon over the west half
    lc = gpd.GeoDataFrame(
        {"FEAT_DESC": ["TREE AREA polygon"]},
        geometry=[box(xmin, ymin, (xmin + xmax) / 2, ymax)],
        crs=CRS,
    )
    lc_path = tmp_path / "land_cover.gpkg"
    lc.to_file(lc_path, driver="GPKG")

    build_buildability_mask(slope, lc_path, None, None, BBOX, CRS)
    frac = _fraction_buildable(tmp_path / "buildability_mask.tif")
    # West half is forest → roughly half excluded
    assert frac < 0.6
    assert frac > 0.4


def _write_dem(path, west_elev, east_elev):
    """DEM with west half = west_elev, east half = east_elev."""
    w = h = 100
    transform = from_bounds(*BBOX, w, h)
    arr = np.full((h, w), east_elev, dtype="float32")
    arr[:, : w // 2] = west_elev
    with rasterio.open(path, "w", driver="GTiff", height=h, width=w, count=1,
                       dtype="float32", crs=CRS, transform=transform, nodata=-32767) as d:
        d.write(arr, 1)
    return path


def test_sea_level_is_excluded(tmp_path):
    slope = _write_flat_slope(tmp_path / "slope.tif")
    dem = _write_dem(tmp_path / "dem.tif", west_elev=-2.0, east_elev=40.0)  # west = ocean
    build_buildability_mask(slope, None, None, None, BBOX, CRS,
                            dem_path=dem, sea_level_m=0.0)
    frac = _fraction_buildable(tmp_path / "buildability_mask.tif")
    # West half at/below sea level → excluded; east half buildable
    assert frac == pytest.approx(0.5, abs=0.02)


def test_buildings_are_excluded(tmp_path):
    slope = _write_flat_slope(tmp_path / "slope.tif")
    xmin, ymin, xmax, ymax = BBOX
    bld = gpd.GeoDataFrame(
        {"id": [1, 2]},
        geometry=[box(xmin + 100, ymin + 100, xmin + 140, ymin + 140),
                  box(xmin + 300, ymin + 300, xmin + 340, ymin + 340)],
        crs=CRS,
    )
    bpath = tmp_path / "buildings.gpkg"
    bld.to_file(bpath, driver="GPKG")

    build_buildability_mask(slope, None, None, bpath, BBOX, CRS, building_buffer_m=10)
    assert _fraction_buildable(tmp_path / "buildability_mask.tif") < 1.0

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


# --- incomplete_land_cover_mask detection ------------------------------------

def _tiny_layer(path):
    xmin, ymin = BBOX[0], BBOX[1]
    gpd.GeoDataFrame({"type": ["TREE AREA"]}, geometry=[box(xmin, ymin, xmin + 10, ymin + 10)],
                     crs=CRS).to_file(path, driver="GPKG")
    return path


def test_mask_built_without_land_cover_or_buildings_is_incomplete(tmp_path, caplog):
    """Regression: the mask was built without land cover or buildings without
    saying so, and score never set incomplete_land_cover_mask."""
    from src.mask import buildability_mask_incomplete

    slope = _write_flat_slope(tmp_path / "slope.tif")
    with caplog.at_level("WARNING", logger="property_finder"):
        build_buildability_mask(slope, None, None, None, BBOX, CRS)
    assert "no land cover" in caplog.text and "no building footprints" in caplog.text
    assert buildability_mask_incomplete(tmp_path) is True
    # Dropping the files in afterwards doesn't fix a mask built without them.
    _tiny_layer(tmp_path / "land_cover.gpkg")
    _tiny_layer(tmp_path / "buildings.gpkg")
    assert buildability_mask_incomplete(tmp_path) is True


def test_mask_built_with_land_cover_and_buildings_is_complete(tmp_path):
    from src.mask import buildability_mask_incomplete

    slope = _write_flat_slope(tmp_path / "slope.tif")
    lc = _tiny_layer(tmp_path / "land_cover.gpkg")
    bld = _tiny_layer(tmp_path / "buildings.gpkg")
    build_buildability_mask(slope, lc, None, bld, BBOX, CRS)
    assert buildability_mask_incomplete(tmp_path) is False
    build_buildability_mask(slope, lc, None, None, BBOX, CRS)  # buildings missing
    assert buildability_mask_incomplete(tmp_path) is True


def test_untagged_mask_is_judged_by_the_input_files(tmp_path):
    """A mask from before the input tags existed (like the real one)."""
    from src.mask import buildability_mask_incomplete

    slope = _write_flat_slope(tmp_path / "slope.tif")
    with rasterio.open(slope) as s:
        meta = s.meta.copy()
    meta.update(dtype="uint8", nodata=255)
    with rasterio.open(tmp_path / "buildability_mask.tif", "w", **meta) as d:
        d.write(np.ones((100, 100), dtype="uint8"), 1)
    assert buildability_mask_incomplete(tmp_path) is True
    _tiny_layer(tmp_path / "land_cover.gpkg")
    _tiny_layer(tmp_path / "buildings.gpkg")
    assert buildability_mask_incomplete(tmp_path) is False


def test_no_mask_at_all_is_incomplete(tmp_path):
    from src.mask import buildability_mask_incomplete

    assert buildability_mask_incomplete(tmp_path) is True


def test_score_data_flags_include_incomplete_land_cover_mask(tmp_path):
    from src.config import Config, Paths, StudyArea
    from src.score import _detect_data_flags

    slope = _write_flat_slope(tmp_path / "slope.tif")
    build_buildability_mask(slope, None, None, None, BBOX, CRS)
    cfg = Config(study_area=StudyArea(bbox=BBOX),
                 paths=Paths(raw=tmp_path / "raw", processed=tmp_path, output=tmp_path / "out"))
    assert _detect_data_flags(cfg)["incomplete_land_cover_mask"] is True

"""Tests for the check-data smoke test (raw-file layers + auto-fetched layers)."""

import logging

import geopandas as gpd
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_bounds
from shapely.geometry import box

from src.check_data import run_check_data
from src.config import Config, Paths, StudyArea

CRS = "EPSG:2961"
BBOX = (380000.0, 4900000.0, 381000.0, 4901000.0)


def _cfg(tmp_path):
    return Config(
        study_area=StudyArea(bbox=BBOX, name="t"),
        paths=Paths(raw=tmp_path / "raw", processed=tmp_path / "processed", output=tmp_path / "out"),
    )


def test_missing_required_layer_reported(tmp_path):
    cfg = _cfg(tmp_path)
    results = run_check_data(cfg, logging.getLogger("test"))
    assert "MISSING (required)" in results["dem"]["status"]


def test_present_vector_layer_ok(tmp_path):
    # A shapefile: ingest_streams reads a .gdb or .shp from data/raw/hydro/
    # (this test used a .gpkg, which ingest never reads).
    cfg = _cfg(tmp_path)
    d = cfg.paths.raw / "hydro"
    d.mkdir(parents=True)
    gpd.GeoDataFrame({"id": [1]}, geometry=[box(0, 0, 1, 1)], crs=CRS).to_file(d / "streams.shp")
    results = run_check_data(cfg, logging.getLogger("test"))
    assert results["streams"]["status"] == "ok"
    assert results["streams"]["feature_count"] == 1


# --- check-data finds exactly what ingest reads ------------------------------

def _vec(path, geom=None, driver=None, **kw):
    from shapely.geometry import LineString

    path.parent.mkdir(parents=True, exist_ok=True)
    gdf = gpd.GeoDataFrame({"id": [1]}, geometry=[geom or LineString([(0, 0), (1, 1)])], crs=CRS)
    gdf.to_file(path, driver=driver, **kw) if driver else gdf.to_file(path)
    return path


def _dem(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", driver="GTiff", height=10, width=10, count=1, dtype="float32",
                       crs=CRS, transform=from_bounds(*BBOX, 10, 10)) as dst:
        dst.write(np.zeros((10, 10), dtype="float32"), 1)
    return path


def test_protected_areas_in_a_subdirectory_found(tmp_path):
    """The real repo keeps them in data/raw/exclusions/protected_areas/*.shp,
    which ingest reads but check-data reported as MISSING (required)."""
    cfg = _cfg(tmp_path)
    shp = _vec(cfg.paths.raw / "exclusions" / "protected_areas" / "geo_export.shp", geom=box(0, 0, 1, 1))
    results = run_check_data(cfg, logging.getLogger("test"))
    assert results["protected_areas"]["status"] == "ok"
    assert results["protected_areas"]["path"] == str(shp)


def test_hrdem_tiles_found(tmp_path):
    cfg = _cfg(tmp_path)
    tile = _dem(cfg.paths.raw / "hrdem" / "tile.tif")
    results = run_check_data(cfg, logging.getLogger("test"))
    assert results["dem"]["status"] == "ok"
    assert results["dem"]["path"] == str(tile)


def test_nshn_geodatabase_found_and_its_stream_layer_checked(tmp_path):
    cfg = _cfg(tmp_path)
    gdb = cfg.paths.raw / "hydro" / "NSHN.gdb"
    _vec(gdb, driver="OpenFileGDB", layer="nshn_v2_wa_line")
    _vec(gdb, geom=box(0, 0, 1, 1), driver="OpenFileGDB", layer="nshn_v2_wa_poly", mode="a")
    results = run_check_data(cfg, logging.getLogger("test"))
    assert results["streams"]["status"] == "ok"
    assert results["streams"]["path"] == str(gdb)
    assert "Line" in results["streams"]["geometry_type"]


def test_buildings_found_recursively(tmp_path):
    cfg = _cfg(tmp_path)
    _vec(cfg.paths.raw / "buildings" / "ns" / "footprints.gpkg", geom=box(0, 0, 1, 1), driver="GPKG")
    results = run_check_data(cfg, logging.getLogger("test"))
    assert results["buildings"]["status"] == "ok"


@pytest.mark.parametrize("layer,relpath,status", [
    ("buildings", "buildings/footprints.geojson", "MISSING (optional)"),
    ("streams", "hydro/streams.gpkg", "MISSING (required)"),
    ("land_cover", "land-cover/cover.gpkg", "MISSING (required)"),
])
def test_formats_ingest_cannot_read_are_not_reported_present(tmp_path, layer, relpath, status):
    cfg = _cfg(tmp_path)
    path = cfg.paths.raw / relpath
    _vec(path, geom=box(0, 0, 1, 1), driver="GeoJSON" if path.suffix == ".geojson" else "GPKG")
    results = run_check_data(cfg, logging.getLogger("test"))
    assert results[layer]["status"] == status


def test_check_data_and_ingest_use_one_discovery_table(tmp_path):
    """check-data reports, per layer, the files ingest reads."""
    from src.raw_layers import RAW_LAYERS, find_raw_files

    cfg = _cfg(tmp_path)
    _dem(cfg.paths.raw / "dem" / "cdem_a.tif")
    _vec(cfg.paths.raw / "land-cover" / "NSTDB_POLY.shp", geom=box(0, 0, 1, 1))
    _vec(cfg.paths.raw / "land-cover" / "NSTDB_LINE.shp")
    results = run_check_data(cfg, logging.getLogger("test"))
    for name in RAW_LAYERS:
        files = find_raw_files(cfg.paths.raw, name)
        if files:
            assert results[name]["files"] == [str(p) for p in files], name
        else:
            assert results[name]["status"].startswith("MISSING"), name
    assert results["land_cover"]["files"] == [str(cfg.paths.raw / "land-cover" / "NSTDB_POLY.shp")]


def test_auto_fetched_layer_not_yet_fetched(tmp_path):
    """flood/wind need no raw file — absent means 'not yet fetched', not an error."""
    cfg = _cfg(tmp_path)
    results = run_check_data(cfg, logging.getLogger("test"))
    assert results["flood"]["status"] == "NOT YET FETCHED (auto)"
    assert results["wind"]["status"] == "NOT YET FETCHED (auto)"


def test_auto_fetched_layer_ok_when_processed_present(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.paths.processed.mkdir(parents=True)
    w, h = 10, 10
    transform = from_bounds(*BBOX, w, h)
    with rasterio.open(
        cfg.paths.processed / "wind.tif", "w", driver="GTiff", height=h, width=w,
        count=1, dtype="float32", crs=CRS, transform=transform,
    ) as dst:
        dst.write(np.full((h, w), 7.0, dtype="float32"), 1)
    results = run_check_data(cfg, logging.getLogger("test"))
    assert results["wind"]["status"] == "ok"


def test_waterbodies_layer_checked(tmp_path):
    cfg = _cfg(tmp_path)
    d = cfg.paths.raw / "water"
    d.mkdir(parents=True)
    gpd.GeoDataFrame({"id": [1]}, geometry=[box(0, 0, 1, 1)], crs=CRS).to_file(d / "wet.gpkg", driver="GPKG")
    results = run_check_data(cfg, logging.getLogger("test"))
    assert results["waterbodies"]["status"] == "ok"

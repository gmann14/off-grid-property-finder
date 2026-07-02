"""Tests for the check-data smoke test (raw-file layers + auto-fetched layers)."""

import logging

import geopandas as gpd
import numpy as np
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
    cfg = _cfg(tmp_path)
    d = cfg.paths.raw / "hydro"
    d.mkdir(parents=True)
    gpd.GeoDataFrame({"id": [1]}, geometry=[box(0, 0, 1, 1)], crs=CRS).to_file(d / "streams.gpkg", driver="GPKG")
    results = run_check_data(cfg, logging.getLogger("test"))
    assert results["streams"]["status"] == "ok"
    assert results["streams"]["feature_count"] == 1


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

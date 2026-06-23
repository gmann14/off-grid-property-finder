"""Tests for wind scoring (GWA raster + terrain-exposure proxy)."""

import numpy as np
import rasterio
from rasterio.transform import from_bounds

from src.config import Config, Paths, StudyArea
from src.constants import EXPOSURE_TPI_THRESHOLDS, WIND_SPEED_THRESHOLDS
from src.scoring.wind import _lookup_score, score_wind, wind_uses_proxy

CRS = "EPSG:2961"
BBOX = (380000.0, 4900000.0, 381000.0, 4901000.0)


def _write_const(path, value, nodata=None):
    w = h = 50
    transform = from_bounds(*BBOX, w, h)
    arr = np.full((h, w), value, dtype="float32")
    kw = dict(driver="GTiff", height=h, width=w, count=1, dtype="float32",
              crs=CRS, transform=transform)
    if nodata is not None:
        kw["nodata"] = nodata
    with rasterio.open(path, "w", **kw) as d:
        d.write(arr, 1)


def _cfg(tmp_path):
    return Config(study_area=StudyArea(bbox=BBOX, name="t"),
                  paths=Paths(raw=tmp_path/"raw", processed=tmp_path, output=tmp_path/"o"))


def test_lookup_speed_and_tpi():
    assert _lookup_score(9.0, WIND_SPEED_THRESHOLDS) == 100
    assert _lookup_score(6.0, WIND_SPEED_THRESHOLDS) == 40
    assert _lookup_score(20.0, EXPOSURE_TPI_THRESHOLDS) == 100   # strong ridge
    assert _lookup_score(0.0, EXPOSURE_TPI_THRESHOLDS) == 40     # flat/neutral
    assert _lookup_score(-10.0, EXPOSURE_TPI_THRESHOLDS) == 15   # valley


def test_wind_prefers_wind_raster(tmp_path, small_grid):
    _write_const(tmp_path / "wind.tif", 8.0, nodata=-999)   # 7.5-8.5 band -> 85
    _write_const(tmp_path / "exposure.tif", 20.0, nodata=-9999)  # would be 100 if used
    cfg = _cfg(tmp_path)
    assert wind_uses_proxy(cfg) is False
    scores = score_wind(small_grid, cfg)
    assert (scores == 85).all()  # used wind raster, not exposure


def test_wind_falls_back_to_exposure(tmp_path, small_grid):
    _write_const(tmp_path / "exposure.tif", 10.0, nodata=-9999)  # 7-15 -> 80
    cfg = _cfg(tmp_path)
    assert wind_uses_proxy(cfg) is True
    scores = score_wind(small_grid, cfg)
    assert (scores == 80).all()


def test_wind_none_available(tmp_path, small_grid):
    scores = score_wind(small_grid, _cfg(tmp_path))
    assert (scores == 0).all()

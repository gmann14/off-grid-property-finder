"""Tests for wind scoring (GWA raster + terrain-exposure proxy)."""

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_bounds

from src.config import Config, Paths, StudyArea
from src.constants import EXPOSURE_TPI_THRESHOLDS, WIND_NO_DATA_SCORE, WIND_NODATA, WIND_SPEED_THRESHOLDS
from src.scoring.bands import band_score
from src.scoring.wind import score_wind, wind_uses_proxy

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
    assert band_score(9.0, WIND_SPEED_THRESHOLDS) == 100
    assert band_score(20.0, EXPOSURE_TPI_THRESHOLDS) == 100   # strong ridge
    assert band_score(0.0, EXPOSURE_TPI_THRESHOLDS) == 40     # flat/neutral
    assert band_score(-10.0, EXPOSURE_TPI_THRESHOLDS) == 15   # valley


@pytest.mark.parametrize("speed, expected", [
    (7.2, 100),   # excellent small-turbine site
    (6.0, 100),   # band edges are inclusive at the bottom
    (5.99, 85),
    (5.0, 85),
    (4.99, 65),
    (4.5, 65),
    (4.49, 40),
    (4.0, 40),
    (3.99, 15),   # below the usual ~4 m/s small-turbine floor
    (0.0, 15),
    (float("inf"), 100),   # the open-ended top band takes +inf too
])
def test_small_turbine_speed_bands(speed, expected):
    assert band_score(speed, WIND_SPEED_THRESHOLDS) == expected


def test_speed_bands_are_contiguous_and_cover_zero_up():
    bands = sorted(WIND_SPEED_THRESHOLDS)
    assert bands[0][0] == 0
    assert bands[-1][1] == float("inf")
    for (_, hi, _), (lo, _, _) in zip(bands, bands[1:]):
        assert hi == lo


def test_wind_prefers_wind_raster(tmp_path, small_grid):
    _write_const(tmp_path / "wind.tif", 5.5, nodata=WIND_NODATA)   # 5.0-6.0 m/s at hub -> 85
    _write_const(tmp_path / "exposure.tif", 20.0, nodata=-9999)  # would be 100 if used
    cfg = _cfg(tmp_path)
    assert wind_uses_proxy(cfg) is False
    scores = score_wind(small_grid, cfg)
    assert (scores == 85).all()  # used wind raster, not exposure


def test_wind_raster_nodata_cells_score_neutral_with_a_warning(tmp_path, small_grid, caplog):
    # A cell entirely over wind.tif nodata has no wind estimate: it scores the
    # neutral WIND_NO_DATA_SCORE (as the exposure proxy does), not the calm
    # band's 15, the run says how many cells that was, and the nodata
    # sentinel is never averaged in as a real speed.
    _write_const(tmp_path / "wind.tif", WIND_NODATA, nodata=WIND_NODATA)
    with caplog.at_level("WARNING", logger="property_finder"):
        scores = score_wind(small_grid, _cfg(tmp_path))
    assert (scores == WIND_NO_DATA_SCORE).all()
    assert f"{len(small_grid)} cell(s) have no wind-raster value" in caplog.text


def test_wind_raster_with_full_coverage_does_not_warn(tmp_path, small_grid, caplog):
    _write_const(tmp_path / "wind.tif", 5.5, nodata=WIND_NODATA)
    with caplog.at_level("WARNING", logger="property_finder"):
        score_wind(small_grid, _cfg(tmp_path))
    assert "no wind-raster value" not in caplog.text


def test_wind_falls_back_to_exposure(tmp_path, small_grid):
    _write_const(tmp_path / "exposure.tif", 10.0, nodata=-9999)  # 7-15 -> 80
    cfg = _cfg(tmp_path)
    assert wind_uses_proxy(cfg) is True
    scores = score_wind(small_grid, cfg)
    assert (scores == 80).all()


def test_wind_none_available(tmp_path, small_grid):
    scores = score_wind(small_grid, _cfg(tmp_path))
    assert (scores == 0).all()

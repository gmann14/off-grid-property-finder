"""Tests for ingest_wind — mocks the /vsicurl/ GWA fetches with local fixture
rasters (one per GWA layer height) so this runs without network access."""

import math
from unittest.mock import patch

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_bounds

from src.config import Config, Paths, StudyArea, WindSettings
from src.constants import WIND_GWA_LAYERS, WIND_NODATA, WIND_SHEAR_ALPHA_MAX, WIND_SHEAR_ALPHA_MIN
from src.ingest import ingest_wind, wind_speed_at_hub
from src.scoring.wind import wind_uses_proxy

CRS = "EPSG:2961"
BBOX = (380000.0, 4900000.0, 381000.0, 4901000.0)  # ~1km study area, Lunenburg-ish
URL10, URL50 = WIND_GWA_LAYERS[10], WIND_GWA_LAYERS[50]


def _make_gwa_fixture(path, value, nodata=-9999.0, fill=None):
    """A small EPSG:4326 raster covering the test area at ~7m/px, standing in
    for a real Global Wind Atlas Canada COG (also EPSG:4326). Resolution
    matters here: too coarse relative to the ~1km test bbox and the windowed
    read rounds to 0 pixels (this bit the first version of this fixture).
    ``fill`` optionally overwrites the whole raster (e.g. with nodata)."""
    w, h = 300, 300
    transform = from_bounds(-64.8, 44.1, -64.2, 44.4, w, h)  # tight around BBOX
    data = np.full((h, w), value, dtype="float32")
    if fill is not None:
        data[:] = fill
    with rasterio.open(
        path, "w", driver="GTiff", height=h, width=w, count=1,
        dtype="float32", crs="EPSG:4326", transform=transform, nodata=nodata,
    ) as dst:
        dst.write(data, 1)


def _fake_rasterio_open(fixtures: dict):
    """Route each /vsicurl/<url> open to the local fixture for that url."""
    real_open = rasterio.open

    def _opener(path, *args, **kwargs):
        if isinstance(path, str) and path.startswith("/vsicurl/"):
            url = path[len("/vsicurl/"):]
            if url not in fixtures:
                raise ConnectionError(f"unexpected url {url}")
            return real_open(fixtures[url])
        return real_open(path, *args, **kwargs)

    return _opener


def _fixtures(tmp_path, v10, v50, **kw):
    f10, f50 = tmp_path / "fake_gwa_10m.tif", tmp_path / "fake_gwa_50m.tif"
    _make_gwa_fixture(f10, v10, **kw.get("kw10", {}))
    _make_gwa_fixture(f50, v50, **kw.get("kw50", {}))
    return {URL10: f10, URL50: f50}


def _cfg(tmp_path, hub=30.0):
    return Config(
        study_area=StudyArea(bbox=BBOX, name="test"),
        paths=Paths(raw=tmp_path / "raw", processed=tmp_path / "processed", output=tmp_path / "out"),
        wind=WindSettings(hub_height_m=hub),
    )


def _ingest(tmp_path, fixtures, hub=30.0):
    cfg = _cfg(tmp_path, hub)
    with patch("rasterio.open", side_effect=_fake_rasterio_open(fixtures)):
        out = ingest_wind(cfg)
    return cfg, out


def _read(out):
    with rasterio.open(out) as src:
        return src.read(1), src.nodata, src.crs.to_string(), src.tags()


def _alpha(v10, v50):
    return math.log(v50 / v10) / math.log(5)


# --- wind_speed_at_hub (pure numpy) ----------------------------------------

def test_hub_speed_unclamped_power_law():
    # v10=4, v50=5 -> alpha = ln(1.25)/ln(5) ~ 0.139 (open-terrain shear, no clamp)
    v, stats = wind_speed_at_hub(np.array([4.0]), np.array([5.0]), 10, 50, 30)
    expected = 4.0 * 3 ** _alpha(4.0, 5.0)
    assert v[0] == pytest.approx(expected, rel=1e-5)
    assert v[0] == pytest.approx(4.658, abs=1e-3)
    assert stats == {"valid": 1, "invalid": 0, "clamped_low": 0, "clamped_high": 0}


def test_hub_speed_anchor_choice_is_irrelevant_when_unclamped():
    # The fitted profile passes through both layers, so either anchor agrees.
    v10, v50 = np.array([3.0, 6.5]), np.array([4.2, 7.4])
    v, _ = wind_speed_at_hub(v10, v50, 10, 50, 30)
    from_10 = v10 * 3 ** (np.log(v50 / v10) / np.log(5))
    from_50 = v50 * 0.6 ** (np.log(v50 / v10) / np.log(5))
    np.testing.assert_allclose(v, from_10, rtol=1e-5)
    np.testing.assert_allclose(v, from_50, rtol=1e-5)


def test_hub_speed_reproduces_layers_at_their_own_heights():
    v10, v50 = np.array([4.0]), np.array([5.0])
    assert wind_speed_at_hub(v10, v50, 10, 50, 10)[0][0] == pytest.approx(4.0, rel=1e-5)
    assert wind_speed_at_hub(v10, v50, 10, 50, 50)[0][0] == pytest.approx(5.0, rel=1e-5)


def test_hub_speed_clamps_high_shear_and_anchors_on_nearer_layer():
    # Forest-canopy case: GWA's 10 m layer is ~0.5 m/s under a 5 m/s 50 m layer
    # (raw alpha ~1.43). Clamped to 0.6 and anchored on 50 m (nearer 30 m in
    # log-height): 5 * 0.6**0.6 ~ 3.68 m/s, not 0.5 * 3**0.6 ~ 0.97 m/s.
    v, stats = wind_speed_at_hub(np.array([0.5]), np.array([5.0]), 10, 50, 30)
    assert _alpha(0.5, 5.0) > WIND_SHEAR_ALPHA_MAX
    assert v[0] == pytest.approx(5.0 * 0.6 ** WIND_SHEAR_ALPHA_MAX, rel=1e-5)
    assert v[0] == pytest.approx(3.680, abs=1e-3)
    assert stats["clamped_high"] == 1 and stats["clamped_low"] == 0


def test_hub_speed_clamps_negative_shear_low():
    # v50 < v10 (raw alpha < 0, e.g. a coastal/model artefact) -> alpha_min.
    v, stats = wind_speed_at_hub(np.array([6.0]), np.array([5.5]), 10, 50, 30)
    assert _alpha(6.0, 5.5) < WIND_SHEAR_ALPHA_MIN
    assert v[0] == pytest.approx(5.5 * 0.6 ** WIND_SHEAR_ALPHA_MIN, rel=1e-5)
    assert v[0] == pytest.approx(5.361, abs=1e-3)
    assert stats["clamped_low"] == 1


def test_hub_speed_low_hub_anchors_on_low_layer():
    # 15 m is nearer 10 m than 50 m in log-height -> anchored on v10.
    v, _ = wind_speed_at_hub(np.array([0.5]), np.array([5.0]), 10, 50, 15)
    assert v[0] == pytest.approx(0.5 * 1.5 ** WIND_SHEAR_ALPHA_MAX, rel=1e-5)


def test_hub_speed_invalid_pixels_get_nodata_and_never_nan_or_inf():
    v10 = np.array([np.nan, 0.0, -1.0, 4.0, np.inf, 4.0])
    v50 = np.array([5.0, 5.0, 5.0, np.nan, 5.0, 0.0])
    v, stats = wind_speed_at_hub(v10, v50, 10, 50, 30)
    assert np.isfinite(v).all()
    assert (v == WIND_NODATA).all()
    assert stats == {"valid": 0, "invalid": 6, "clamped_low": 0, "clamped_high": 0}


def test_hub_speed_rejects_bad_heights():
    with pytest.raises(ValueError):
        wind_speed_at_hub(np.array([4.0]), np.array([5.0]), 50, 10, 30)
    with pytest.raises(ValueError):
        wind_speed_at_hub(np.array([4.0]), np.array([5.0]), 10, 50, 0)
    with pytest.raises(ValueError):
        wind_speed_at_hub(np.array([4.0]), np.array([4.0, 5.0]), 10, 50, 30)


# --- ingest_wind (mocked /vsicurl/) ----------------------------------------

def test_ingest_wind_writes_hub_height_raster(tmp_path):
    _, out = _ingest(tmp_path, _fixtures(tmp_path, 4.0, 5.0))
    assert out is not None and out.exists()
    data, nodata, crs, tags = _read(out)
    assert crs == CRS
    assert nodata == WIND_NODATA
    assert np.isfinite(data).all()
    valid = data[data != nodata]
    assert valid.size > 0
    assert np.allclose(valid, 4.0 * 3 ** _alpha(4.0, 5.0), atol=0.01)  # ~4.66 m/s
    assert tags["hub_height_m"] == "30"
    assert URL10 in tags["gwa_layers"] and URL50 in tags["gwa_layers"]


def test_ingest_wind_clamps_forest_shear(tmp_path):
    _, out = _ingest(tmp_path, _fixtures(tmp_path, 0.5, 5.0))
    data, nodata, _, _ = _read(out)
    valid = data[data != nodata]
    assert np.allclose(valid, 5.0 * 0.6 ** WIND_SHEAR_ALPHA_MAX, atol=0.01)  # ~3.68


def test_ingest_wind_honours_configured_hub_height(tmp_path):
    _, out = _ingest(tmp_path, _fixtures(tmp_path, 4.0, 5.0), hub=20.0)
    data, nodata, _, tags = _read(out)
    valid = data[data != nodata]
    assert np.allclose(valid, 4.0 * 2 ** _alpha(4.0, 5.0), atol=0.01)  # ~4.40
    assert tags["hub_height_m"] == "20"


def test_ingest_wind_nodata_layer_yields_none_not_nan(tmp_path):
    # Every 10 m pixel is the source nodata value -> no valid pixels -> no file
    # (an all-nodata wind.tif would silently clear the proxy flag).
    fx = _fixtures(tmp_path, 4.0, 5.0, kw10={"fill": -9999.0})
    cfg, out = _ingest(tmp_path, fx)
    assert out is None
    assert not (cfg.paths.processed / "wind.tif").exists()


def test_ingest_wind_zero_speed_pixels_become_nodata(tmp_path):
    # v10 == 0 everywhere (not declared nodata) -> alpha undefined -> nodata,
    # and with nothing valid left, no file.
    fx = _fixtures(tmp_path, 0.0, 5.0, kw10={"nodata": None})
    cfg, out = _ingest(tmp_path, fx)
    assert out is None
    assert not (cfg.paths.processed / "wind.tif").exists()


def test_ingest_wind_clears_the_proxy_flag(tmp_path):
    fx = _fixtures(tmp_path, 4.0, 5.0)
    cfg = _cfg(tmp_path)
    assert wind_uses_proxy(cfg) is True  # no wind.tif yet
    with patch("rasterio.open", side_effect=_fake_rasterio_open(fx)):
        ingest_wind(cfg)
    assert wind_uses_proxy(cfg) is False


def test_ingest_wind_skips_if_already_processed(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.paths.processed.mkdir(parents=True)
    sentinel = cfg.paths.processed / "wind.tif"
    sentinel.write_bytes(b"already here")

    # Must not even attempt to open the network path
    with patch("rasterio.open") as mock_open:
        out = ingest_wind(cfg)
    mock_open.assert_not_called()
    assert out == sentinel


def test_ingest_wind_returns_none_on_fetch_failure(tmp_path):
    cfg = _cfg(tmp_path)
    with patch("rasterio.open", side_effect=ConnectionError("timed out")):
        out = ingest_wind(cfg)
    assert out is None
    assert not (cfg.paths.processed / "wind.tif").exists()


def test_ingest_wind_returns_none_if_second_layer_fails(tmp_path):
    fx = _fixtures(tmp_path, 4.0, 5.0)
    del fx[URL50]  # 10 m reads fine, 50 m "times out"
    cfg, out = _ingest(tmp_path, fx)
    assert out is None
    assert not (cfg.paths.processed / "wind.tif").exists()
    assert not list(cfg.paths.processed.glob("*.partial.tif"))  # glob of a missing dir is empty

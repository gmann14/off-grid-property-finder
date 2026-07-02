"""Tests for ingest_wind — mocks the /vsicurl/ GWA fetch with a local fixture
raster so this runs without network access."""

from unittest.mock import patch

import numpy as np
import rasterio
from rasterio.transform import from_bounds

from src.config import Config, Paths, StudyArea
from src.ingest import ingest_wind
from src.scoring.wind import wind_uses_proxy

CRS = "EPSG:2961"
BBOX = (380000.0, 4900000.0, 381000.0, 4901000.0)  # ~1km study area, Lunenburg-ish


def _make_gwa_fixture(path):
    """A small EPSG:4326 raster covering the test area at ~7m/px, standing in
    for the real Global Wind Atlas Canada COG (also EPSG:4326). Resolution
    matters here: too coarse relative to the ~1km test bbox and the windowed
    read rounds to 0 pixels (this bit the first version of this fixture)."""
    w, h = 300, 300
    transform = from_bounds(-64.8, 44.1, -64.2, 44.4, w, h)  # tight around BBOX
    data = np.full((h, w), 7.2, dtype="float32")  # a plausible mean wind speed
    with rasterio.open(
        path, "w", driver="GTiff", height=h, width=w, count=1,
        dtype="float32", crs="EPSG:4326", transform=transform, nodata=-9999.0,
    ) as dst:
        dst.write(data, 1)


def _fake_rasterio_open(fixture_path):
    real_open = rasterio.open

    def _opener(path, *args, **kwargs):
        if isinstance(path, str) and path.startswith("/vsicurl/"):
            return real_open(fixture_path)
        return real_open(path, *args, **kwargs)

    return _opener


def _cfg(tmp_path):
    return Config(
        study_area=StudyArea(bbox=BBOX, name="test"),
        paths=Paths(raw=tmp_path / "raw", processed=tmp_path / "processed", output=tmp_path / "out"),
    )


def test_ingest_wind_writes_reprojected_raster(tmp_path):
    fixture = tmp_path / "fake_gwa.tif"
    _make_gwa_fixture(fixture)
    cfg = _cfg(tmp_path)

    with patch("rasterio.open", side_effect=_fake_rasterio_open(fixture)):
        out = ingest_wind(cfg)

    assert out is not None
    assert out.exists()
    with rasterio.open(out) as src:
        assert src.crs.to_string() == CRS
        data = src.read(1)
    valid = data[np.isfinite(data)]
    assert valid.size > 0
    assert np.allclose(valid, 7.2, atol=0.1)  # matches the fixture's constant value


def test_ingest_wind_clears_the_proxy_flag(tmp_path):
    fixture = tmp_path / "fake_gwa.tif"
    _make_gwa_fixture(fixture)
    cfg = _cfg(tmp_path)

    assert wind_uses_proxy(cfg) is True  # no wind.tif yet
    with patch("rasterio.open", side_effect=_fake_rasterio_open(fixture)):
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

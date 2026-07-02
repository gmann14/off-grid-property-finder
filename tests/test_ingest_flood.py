"""Test the flood-raster polygonize helper and the empty-vs-failed distinction."""

from unittest.mock import MagicMock, patch

import numpy as np
from rasterio.io import MemoryFile
from rasterio.transform import from_bounds

from src.config import Config, Paths, StudyArea
from src.ingest import _mask_to_polygons, fetch_flood_polygons, ingest_flood

CRS = "EPSG:2961"
BBOX = (380000.0, 4900000.0, 381000.0, 4901000.0)  # 1km x 1km


def _png_bytes(w, h, alpha):
    """A 4-band RGBA PNG for mocking the ArcGIS export (built via rasterio,
    the same library fetch_flood_polygons uses to decode the real response)."""
    arr = np.zeros((4, h, w), dtype="uint8")
    arr[3] = alpha
    with MemoryFile() as mem:
        with mem.open(driver="PNG", height=h, width=w, count=4, dtype="uint8") as dst:
            dst.write(arr)
        return mem.read()


def _mock_ok_response(content):
    resp = MagicMock()
    resp.content = content
    resp.raise_for_status = MagicMock()
    return resp


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


# --- fetch_flood_polygons: empty (checked, no flood) vs None (fetch failed) ---

def test_fetch_flood_empty_when_no_flooded_pixels():
    """Export succeeds but every pixel is transparent (no flooding) -> an
    EMPTY GeoDataFrame, not None — the caller needs to know we actually checked."""
    with patch("requests.get", return_value=_mock_ok_response(_png_bytes(10, 10, alpha=0))):
        result = fetch_flood_polygons(BBOX, working_crs=CRS)
    assert result is not None
    assert result.empty


def test_fetch_flood_polygons_when_flooded_pixels_present():
    with patch("requests.get", return_value=_mock_ok_response(_png_bytes(10, 10, alpha=255))):
        result = fetch_flood_polygons(BBOX, working_crs=CRS)
    assert result is not None
    assert not result.empty
    assert (result["exclusion_reason"] == "flood_zone").all()


def test_fetch_flood_none_on_network_failure():
    with patch("requests.get", side_effect=ConnectionError("timed out")):
        result = fetch_flood_polygons(BBOX, working_crs=CRS)
    assert result is None


# --- ingest_flood: cache the empty sentinel, but never cache a failure ------

def _flood_config(tmp_path):
    return Config(
        study_area=StudyArea(bbox=BBOX, name="t"),
        paths=Paths(raw=tmp_path / "raw", processed=tmp_path / "processed", output=tmp_path / "out"),
    )


def test_ingest_flood_caches_empty_result(tmp_path):
    cfg = _flood_config(tmp_path)
    with patch("requests.get", return_value=_mock_ok_response(_png_bytes(10, 10, alpha=0))):
        out = ingest_flood(cfg)
    assert out is not None
    assert out.exists()  # the empty sentinel IS written


def test_ingest_flood_does_not_cache_on_failure(tmp_path):
    """A network failure must not write flood.gpkg — otherwise a transient
    outage would be indistinguishable from 'no flood zones here' forever."""
    cfg = _flood_config(tmp_path)
    with patch("requests.get", side_effect=ConnectionError("timed out")):
        out = ingest_flood(cfg)
    assert out is None
    assert not (cfg.paths.processed / "flood.gpkg").exists()

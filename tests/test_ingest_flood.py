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


# --- export size follows the bbox aspect ratio -------------------------------

def _aspect_widening_server(flood_rect):
    """A mocked MapServer export that, like ArcGIS, widens the requested
    extent to the image's aspect ratio before rendering, and draws
    ``flood_rect`` (world coordinates) as opaque pixels."""
    def get(url, params=None, timeout=None):
        w, h = (int(v) for v in params["size"].split(","))
        xmin, ymin, xmax, ymax = (float(v) for v in params["bbox"].split(","))
        cx, cy, bw, bh = (xmin + xmax) / 2, (ymin + ymax) / 2, xmax - xmin, ymax - ymin
        if bw / bh < w / h:
            bw = bh * w / h
        else:
            bh = bw * h / w
        x0, y1 = cx - bw / 2, cy + bh / 2
        xs = x0 + (np.arange(w) + 0.5) * bw / w
        ys = y1 - (np.arange(h) + 0.5) * bh / h
        fx0, fy0, fx1, fy1 = flood_rect
        inside = ((ys[:, None] >= fy0) & (ys[:, None] <= fy1)
                  & (xs[None, :] >= fx0) & (xs[None, :] <= fx1))
        return _mock_ok_response(_png_bytes(w, h, alpha=np.where(inside, 255, 0)))
    return get


def test_fetch_flood_requests_image_with_bbox_aspect_ratio():
    """For a non-square bbox, a square image made ArcGIS widen the extent, and
    georeferencing the result to the requested bbox put the polygons in the
    wrong place."""
    bbox = (380000.0, 4900000.0, 382000.0, 4901000.0)   # 2 km x 1 km
    flood = (380500.0, 4900200.0, 380900.0, 4900600.0)
    with patch("requests.get", side_effect=_aspect_widening_server(flood)) as mock_get:
        result = fetch_flood_polygons(bbox, size=200, working_crs=CRS)
    assert mock_get.call_args.kwargs["params"]["size"] == "200,100"
    px = 2000 / 200
    np.testing.assert_allclose(result.total_bounds, flood, atol=px)


def test_fetch_flood_square_bbox_request_unchanged():
    from src.constants import FLOOD_EXPORT_SIZE

    with patch("requests.get", return_value=_mock_ok_response(_png_bytes(10, 10, alpha=0))) as mock_get:
        fetch_flood_polygons((360000, 4880000, 410000, 4930000), working_crs=CRS)
    assert mock_get.call_args.kwargs["params"]["size"] == f"{FLOOD_EXPORT_SIZE},{FLOOD_EXPORT_SIZE}"


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

"""The study bbox in another CRS (NSPRD query envelope, GWA read window) is
taken from densified edges, not just the four corners."""

from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

import geopandas as gpd
import numpy as np
import pytest
import rasterio
import rasterio.windows
from pyproj import Transformer
from rasterio.transform import from_bounds, from_origin
from shapely.geometry import box

from src.ingest import _read_gwa_layer, _transform_bounds, fetch_nsprd_parcels

CRS = "EPSG:2961"
CONFIG_BBOX = (360000, 4880000, 410000, 4930000)   # config.yaml study area
# Spans the UTM 20N central meridian (63°W, easting 500000): the top edge bulges
# north past its corners in lat/lon, the bottom edge's corners are the extremes.
CM_BBOX = (450000, 4880000, 550000, 4930000)


def _corner_bounds(bbox, dst_crs):
    """What the code did before: reproject the box's 4 corners only."""
    return tuple(gpd.GeoSeries([box(*bbox)], crs=CRS).to_crs(dst_crs).total_bounds)


def _edge_points(bbox, n=201):
    xmin, ymin, xmax, ymax = bbox
    t = np.linspace(0, 1, n)
    xs = np.concatenate([xmin + t * (xmax - xmin), np.full(n, xmax),
                         xmax - t * (xmax - xmin), np.full(n, xmin)])
    ys = np.concatenate([np.full(n, ymin), ymin + t * (ymax - ymin),
                         np.full(n, ymax), ymax - t * (ymax - ymin)])
    return Transformer.from_crs(CRS, "EPSG:4326", always_xy=True).transform(xs, ys)


def _contains_edges(bounds, bbox):
    lon, lat = _edge_points(bbox)
    return (lon.min() >= bounds[0] and lat.min() >= bounds[1]
            and lon.max() <= bounds[2] and lat.max() <= bounds[3])


def test_configured_bbox_bounds_are_identical_to_the_corner_method():
    assert _transform_bounds(CONFIG_BBOX, CRS, "EPSG:4326") == _corner_bounds(CONFIG_BBOX, "EPSG:4326")


def test_corner_method_misses_the_bulge_of_a_meridian_crossing_bbox():
    assert not _contains_edges(_corner_bounds(CM_BBOX, "EPSG:4326"), CM_BBOX)
    assert _contains_edges(_transform_bounds(CM_BBOX, CRS, "EPSG:4326"), CM_BBOX)


# --- NSPRD query envelope --------------------------------------------------

def _nsprd_envelope(bbox):
    resp = MagicMock()
    resp.json.return_value = {"type": "FeatureCollection", "features": []}
    with patch("requests.get", return_value=resp) as mock_get:
        fetch_nsprd_parcels(bbox)
    geom = parse_qs(urlparse(mock_get.call_args.args[0]).query)["geometry"][0]
    return tuple(float(v) for v in geom.split(","))


def test_nsprd_envelope_unchanged_for_configured_bbox():
    assert _nsprd_envelope(CONFIG_BBOX) == _corner_bounds(CONFIG_BBOX, "EPSG:4326")


def test_nsprd_envelope_covers_a_meridian_crossing_bbox():
    assert _contains_edges(_nsprd_envelope(CM_BBOX), CM_BBOX)


# --- GWA read window -------------------------------------------------------

def _gwa_window_bounds(tmp_path, bbox):
    """The bounds _read_gwa_layer asks for a window of, on a local stand-in
    for the GWA COG (EPSG:4326)."""
    path = tmp_path / "gwa.tif"
    w, h = 400, 200
    with rasterio.open(path, "w", driver="GTiff", height=h, width=w, count=1, dtype="float32",
                       crs="EPSG:4326", transform=from_bounds(-65.5, 43.5, -61.5, 45.5, w, h)) as dst:
        dst.write(np.full((h, w), 6.0, dtype="float32"), 1)
    real_open, real_from_bounds = rasterio.open, rasterio.windows.from_bounds
    seen = []

    def fake_open(fp, *args, **kwargs):
        return real_open(path if str(fp).startswith("/vsicurl/") else fp, *args, **kwargs)

    def spy_from_bounds(left, bottom, right, top, *args, **kwargs):
        seen.append((left, bottom, right, top))
        return real_from_bounds(left, bottom, right, top, *args, **kwargs)

    xmin, ymin, xmax, ymax = bbox
    shape = (int((ymax - ymin) // 1000), int((xmax - xmin) // 1000))
    with patch("rasterio.open", side_effect=fake_open), \
            patch("rasterio.windows.from_bounds", side_effect=spy_from_bounds):
        _read_gwa_layer("https://example.invalid/gwa.tif", bbox, CRS,
                        from_origin(xmin, ymax, 1000, 1000), shape)
    assert len(seen) == 1
    return tuple(float(v) for v in seen[0])


def test_gwa_window_unchanged_for_configured_bbox(tmp_path):
    assert _gwa_window_bounds(tmp_path, CONFIG_BBOX) == pytest.approx(
        _corner_bounds(CONFIG_BBOX, "EPSG:4326"), abs=0, rel=0)


def test_gwa_window_covers_a_meridian_crossing_bbox(tmp_path):
    assert _contains_edges(_gwa_window_bounds(tmp_path, CM_BBOX), CM_BBOX)

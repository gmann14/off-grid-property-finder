"""Tests for DEM derivatives (slope, aspect) around nodata."""

import numpy as np
import rasterio
from rasterio.transform import from_bounds

from src.dem import generate_aspect, generate_slope

CRS = "EPSG:2961"
BBOX = (380000.0, 4900000.0, 381000.0, 4901000.0)
NODATA = -32767.0


def _holed_dem(path):
    """10 m tilted plane (rising 1 m per 10 m northward, ~5.7 deg) with a
    20x20 px nodata block in the middle."""
    h = w = 100
    rows = np.arange(h, dtype="float32")[:, None]
    dem = np.repeat(200.0 - rows, w, axis=1).astype("float32")
    dem[40:60, 40:60] = NODATA
    with rasterio.open(path, "w", driver="GTiff", height=h, width=w, count=1,
                       dtype="float32", crs=CRS, transform=from_bounds(*BBOX, w, h),
                       nodata=NODATA) as d:
        d.write(dem, 1)
    return path


def _read(path):
    with rasterio.open(path) as s:
        return s.read(1), s.nodata


def test_slope_propagates_dem_nodata(tmp_path):
    """Regression: the hole read as slope 0 (flat, buildable) and its rim as a
    ~90 deg cliff down to the -32767 sentinel."""
    slope, nd = _read(generate_slope(_holed_dem(tmp_path / "dem.tif"), tmp_path / "slope.tif"))
    assert nd == -9999
    assert (slope[40:60, 40:60] == nd).all()
    valid = slope[slope != nd]
    expected = np.degrees(np.arctan(0.1))
    assert np.allclose(valid, expected, atol=0.01)
    # only the hole and the pixels on its four sides (whose central
    # differences reach into it) are lost; the corners' diagonal neighbours
    # don't enter the gradient
    assert (slope == nd).sum() == 20 * 20 + 4 * 20


def test_aspect_propagates_dem_nodata(tmp_path):
    aspect, nd = _read(generate_aspect(_holed_dem(tmp_path / "dem.tif"), tmp_path / "aspect.tif"))
    assert (aspect[40:60, 40:60] == nd).all()
    valid = aspect[aspect != nd]
    # rises to the north, so the slope faces south (180 deg)
    assert np.allclose(valid, 180.0, atol=0.01)


def test_dem_without_nodata_tag_keeps_every_pixel(tmp_path):
    h = w = 20
    dem = np.repeat(np.arange(h, dtype="float32")[::-1, None], w, axis=1)
    path = tmp_path / "dem.tif"
    with rasterio.open(path, "w", driver="GTiff", height=h, width=w, count=1,
                       dtype="float32", crs=CRS, transform=from_bounds(*BBOX, w, h)) as d:
        d.write(dem, 1)
    slope, nd = _read(generate_slope(path, tmp_path / "slope.tif"))
    assert (slope != nd).all()

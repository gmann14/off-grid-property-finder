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


# --- flow routing with the sea in the DEM (H6) ------------------------------
def _coast_dem(path):
    """10 m DEM: land in the west 60 columns falling 1 m per 10 m eastward to
    0.5 m at the shore, sea (0 m with -1 m hollows, as in the real DEM) in
    the east 40."""
    h, w = 100, 100
    cols = np.arange(w, dtype="float32")
    row = np.where(cols < 60, 0.5 + (59 - cols), 0.0).astype("float32")
    dem = np.tile(row, (h, 1))
    dem[20:30, 70:80] = -1.0
    dem[60:70, 85:95] = -1.0
    with rasterio.open(path, "w", driver="GTiff", height=h, width=w, count=1,
                       dtype="float32", crs=CRS, transform=from_bounds(*BBOX, w, h),
                       nodata=NODATA) as d:
        d.write(dem, 1)
    return path


def test_flow_routing_leaves_the_sea_out(tmp_path):
    """Regression: sea pixels (<= 0 m) were routed like land, so the sea
    piled up the coast's flow (the real raster's max, 2.9 M cells, is at sea;
    the land's is 0.6 M) and its hollows were breached through the shore."""
    from src.dem import generate_flow_accumulation

    out = generate_flow_accumulation(_coast_dem(tmp_path / "dem.tif"),
                                     tmp_path / "flow_accumulation.tif", target_res_m=20.0)
    facc, nd = _read(out)
    routing, rnd = _read(tmp_path / "dem_flowres.tif")
    assert facc.shape == (50, 50)
    assert (routing[:, 30:] == rnd).all()          # sea is nodata in the routing DEM
    assert (facc[:, 30:] == nd).all()              # and accumulates nothing
    land = facc[:, :30]
    assert (land != nd).all()
    # each row drains east to the shore (30 cells), and the flat shore column
    # passes a few rows' worth along before it leaves: nothing like the
    # 1,500 land cells the sea used to collect
    assert 30 <= land.max() < 100


def test_flow_routing_rebuilds_a_stale_conditioned_dem(tmp_path):
    """A conditioned DEM left by an earlier run (e.g. routed with the sea in)
    must not be reused once the routing DEM is rewritten."""
    from src.dem import generate_flow_accumulation

    dem = _coast_dem(tmp_path / "dem.tif")
    stale = np.full((50, 50), 7.0, dtype="float32")  # flat: D8 can't route it
    with rasterio.open(tmp_path / "dem_flowres_filled.tif", "w", driver="GTiff", height=50,
                       width=50, count=1, dtype="float32", crs=CRS,
                       transform=from_bounds(*BBOX, 50, 50), nodata=NODATA) as d:
        d.write(stale, 1)
    facc, nd = _read(generate_flow_accumulation(dem, tmp_path / "flow_accumulation.tif",
                                                target_res_m=20.0))
    assert (facc[:, 30:] == nd).all()
    assert 30 <= facc[:, :30].max() < 100

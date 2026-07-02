"""Parity tests for grid_zonal_stats against rasterstats (its predecessor and
the library it replaced for performance — see commit 323b53e). rasterstats is
still a declared dependency specifically so it can serve as the oracle here.
"""

import numpy as np
import geopandas as gpd
import pytest
import rasterio
from rasterio.transform import from_bounds
from rasterstats import zonal_stats as rasterstats_zonal_stats
from shapely.geometry import box

from src.zonal import grid_zonal_stats

CRS = "EPSG:2961"
BBOX = (0.0, 0.0, 1000.0, 1000.0)  # 1km x 1km, 10m pixels -> 100x100 grid
NODATA = -9999.0


@pytest.fixture
def raster_path(tmp_path):
    """A 100x100 raster of pseudo-random values with a block of nodata."""
    rng = np.random.RandomState(7)
    w, h = 100, 100
    data = rng.uniform(0, 100, size=(h, w)).astype("float32")
    data[10:20, 10:20] = NODATA  # a nodata block inside the raster
    transform = from_bounds(*BBOX, w, h)
    path = tmp_path / "test.tif"
    with rasterio.open(
        path, "w", driver="GTiff", height=h, width=w, count=1,
        dtype="float32", crs=CRS, transform=transform, nodata=NODATA,
    ) as dst:
        dst.write(data, 1)
    return path


@pytest.fixture
def polygons():
    """A grid of 25 cells (5x5, 200m each) tiling the raster, PLUS one entirely
    outside the raster (must come back empty/NaN in both implementations)."""
    cells = [box(x, y, x + 200, y + 200) for x in range(0, 1000, 200) for y in range(0, 1000, 200)]
    cells.append(box(2000, 2000, 2200, 2200))  # fully outside
    return gpd.GeoSeries(cells, crs=CRS)


def _rasterstats_array(polygons, raster_path, stat, nodata):
    result = rasterstats_zonal_stats(polygons, str(raster_path), stats=[stat], nodata=nodata)
    return np.array([r[stat] if r[stat] is not None else np.nan for r in result])


class TestZonalParity:
    @pytest.mark.parametrize("stat", ["mean", "min", "max", "range"])
    def test_matches_rasterstats(self, raster_path, polygons, stat):
        ours = grid_zonal_stats(polygons, raster_path, stats=(stat,), nodata=NODATA)[stat]
        theirs = _rasterstats_array(polygons, raster_path, stat, NODATA)

        both_valid = ~np.isnan(ours) & ~np.isnan(theirs)
        assert both_valid.sum() >= len(polygons) - 1  # only the fully-outside one may be NaN
        assert np.allclose(ours[both_valid], theirs[both_valid], atol=1e-4)
        # NaN-ness itself must match (empty zones agree between implementations)
        assert (np.isnan(ours) == np.isnan(theirs)).all()

    def test_count_matches_rasterstats(self, raster_path, polygons):
        ours = grid_zonal_stats(polygons, raster_path, stats=("count",), nodata=NODATA)["count"]
        result = rasterstats_zonal_stats(polygons, str(raster_path), stats=["count"], nodata=NODATA)
        theirs = np.array([r["count"] for r in result])
        assert (ours == theirs).all()

    def test_outside_raster_polygon_is_empty(self, raster_path, polygons):
        result = grid_zonal_stats(polygons, raster_path, stats=("mean", "count"))
        assert np.isnan(result["mean"][-1])  # the last polygon is fully outside
        assert result["count"][-1] == 0

    def test_nodata_block_excluded_from_mean(self, raster_path):
        """The polygon covering the injected nodata block should have a mean
        computed only from the valid pixels within it, matching rasterstats."""
        # Pixel (10:20, 10:20) in a 100x100 grid over (0,0,1000,1000) -> roughly
        # world coords (100,800)-(200,900) depending on row/col orientation;
        # cover generously to guarantee overlap with the nodata block.
        poly = gpd.GeoSeries([box(50, 750, 250, 950)], crs=CRS)
        ours = grid_zonal_stats(poly, raster_path, stats=("mean", "count"), nodata=NODATA)
        theirs = rasterstats_zonal_stats(poly, str(raster_path), stats=["mean", "count"], nodata=NODATA)
        assert ours["mean"][0] == pytest.approx(theirs[0]["mean"], abs=1e-4)
        assert ours["count"][0] == theirs[0]["count"]


class TestZonalFrac:
    def test_frac_matches_manual_computation(self, raster_path):
        """No rasterstats equivalent for `frac` — verify against a direct
        numpy computation over the same window instead."""
        poly = gpd.GeoSeries([box(0, 0, 200, 200)], crs=CRS)
        lo, hi = 20.0, 60.0
        result = grid_zonal_stats(poly, raster_path, stats=("frac",), nodata=NODATA, frac_range=(lo, hi))

        with rasterio.open(raster_path) as src:
            full = src.read(1)
        # The 200x200m polygon at the origin covers pixels [80:100, 0:20]
        # (10m pixels, raster origin at row0=top=y=1000) — read the exact
        # window rather than re-deriving the transform to keep this independent.
        from rasterio.windows import from_bounds as window_from_bounds
        with rasterio.open(raster_path) as src:
            win = window_from_bounds(0, 0, 200, 200, src.transform)
            window_data = src.read(1, window=win)
        valid = window_data[window_data != NODATA]
        expected_frac = ((valid >= lo) & (valid <= hi)).mean()
        assert result["frac"][0] == pytest.approx(expected_frac, abs=1e-6)

    def test_frac_requires_range(self, raster_path):
        poly = gpd.GeoSeries([box(0, 0, 200, 200)], crs=CRS)
        with pytest.raises(ValueError):
            grid_zonal_stats(poly, raster_path, stats=("frac",), nodata=NODATA)

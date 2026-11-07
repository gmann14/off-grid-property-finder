"""DEM ingest: tile mosaicking, CDEM gap fill, coverage warning, and the
single-file path staying byte-identical to the original implementation."""

import logging

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_bounds
from shapely.geometry import box

from src.config import Config, Paths, StudyArea
from src.ingest import ingest_dem

CRS = "EPSG:2961"
BBOX = (380000.0, 4900000.0, 381000.0, 4901000.0)  # 1 km x 1 km
NODATA = -32767.0


def _cfg(tmp_path):
    return Config(
        study_area=StudyArea(bbox=BBOX, name="t"),
        paths=Paths(raw=tmp_path / "raw", processed=tmp_path / "processed", output=tmp_path / "out"),
    )


def _write_tile(path, bounds, value, res=10.0, crs=CRS, nodata=NODATA):
    """A constant-valued DEM tile (or a south-north ramp when value is None)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    xmin, ymin, xmax, ymax = bounds
    w, h = int(round((xmax - xmin) / res)), int(round((ymax - ymin) / res))
    if value is None:
        arr = np.tile(np.linspace(200, 50, h, dtype="float32")[:, None], (1, w))
    else:
        arr = np.full((h, w), value, dtype="float32")
    with rasterio.open(
        path, "w", driver="GTiff", height=h, width=w, count=1, dtype="float32",
        crs=crs, transform=from_bounds(xmin, ymin, xmax, ymax, w, h), nodata=nodata,
    ) as dst:
        dst.write(arr, 1)
    return path


def _sample(path, x, y):
    with rasterio.open(path) as src:
        return float(next(src.sample([(x, y)]))[0])


def _covers_bbox(path):
    with rasterio.open(path) as src:
        b = src.bounds
    return b.left <= BBOX[0] and b.bottom <= BBOX[1] and b.right >= BBOX[2] and b.top >= BBOX[3]


XMIN, YMIN, XMAX, YMAX = BBOX
MID = (XMIN + XMAX) / 2
WEST = (XMIN - 50, YMIN - 50, MID, YMAX + 50)
EAST = (MID, YMIN - 50, XMAX + 50, YMAX + 50)
FULL = (XMIN - 50, YMIN - 50, XMAX + 50, YMAX + 50)


def test_all_hrdem_tiles_are_mosaicked(tmp_path):
    """Two HRDEM tiles each covering half the study area: both are used, not
    just whichever the directory listing returned first."""
    cfg = _cfg(tmp_path)
    _write_tile(cfg.paths.raw / "hrdem" / "tile_a.tif", WEST, 10.0)
    _write_tile(cfg.paths.raw / "hrdem" / "tile_b.tif", EAST, 20.0)

    out = ingest_dem(cfg)
    assert _covers_bbox(out)
    assert _sample(out, XMIN + 100, YMIN + 500) == 10.0
    assert _sample(out, XMAX - 100, YMIN + 500) == 20.0
    with rasterio.open(out) as src:
        arr = src.read(1)
    assert (arr != NODATA).all()
    assert not (cfg.paths.processed / "dem_mosaic.tif").exists()


def test_tiles_in_different_crs_are_mosaicked(tmp_path):
    cfg = _cfg(tmp_path)
    _write_tile(cfg.paths.raw / "hrdem" / "tile_a.tif", WEST, 10.0)
    # same ground as EAST, stored in NAD83 / UTM 20N (EPSG:26920) coordinates
    _write_tile(cfg.paths.raw / "hrdem" / "tile_b.tif", EAST, 20.0, crs="EPSG:26920")

    out = ingest_dem(cfg)
    assert _covers_bbox(out)
    assert _sample(out, XMIN + 100, YMIN + 500) == 10.0
    assert _sample(out, XMAX - 100, YMIN + 500) == 20.0


def test_partial_hrdem_is_filled_from_cdem(tmp_path, caplog):
    """One HRDEM tile covering half the study area must not replace a
    full-coverage CDEM: the gap is filled from CDEM."""
    cfg = _cfg(tmp_path)
    _write_tile(cfg.paths.raw / "hrdem" / "tile_a.tif", WEST, 10.0, res=5.0)
    _write_tile(cfg.paths.raw / "dem" / "cdem_full.tif", FULL, 99.0, res=20.0)

    with caplog.at_level(logging.INFO, logger="property_finder"):
        out = ingest_dem(cfg)
    assert _covers_bbox(out)
    with rasterio.open(out) as src:
        assert src.res == pytest.approx((5.0, 5.0))  # HRDEM resolution is kept
        arr = src.read(1)
    assert (arr != NODATA).all()
    assert _sample(out, XMIN + 100, YMIN + 500) == 10.0   # HRDEM where it has data
    assert _sample(out, XMAX - 100, YMIN + 500) == 99.0   # CDEM in the gap
    assert "from CDEM" in caplog.text
    assert not list(cfg.paths.processed.glob("dem_*mosaic*.tif"))


def test_partial_dem_without_fill_warns_with_covered_share(tmp_path, caplog):
    cfg = _cfg(tmp_path)
    _write_tile(cfg.paths.raw / "dem" / "cdem_west.tif", WEST, 10.0)

    with caplog.at_level(logging.WARNING, logger="property_finder"):
        out = ingest_dem(cfg)
    assert out is not None
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("50.0% of the study area" in m for m in warnings), warnings


# --- failed writes are not cached -------------------------------------------

def test_failed_dem_write_is_not_cached(tmp_path, monkeypatch):
    """A dem.tif whose write fails part-way (disk full) must not be left in
    place: ingest skips when dem.tif exists, so it would be used forever."""
    from pathlib import Path

    import rasterio.errors

    cfg = _cfg(tmp_path)
    _write_tile(cfg.paths.raw / "hrdem" / "tile.tif", FULL, 10.0)
    real_open = rasterio.open

    class _FailingWriter:
        def __init__(self, ds):
            self.ds = ds

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.ds.close()
            return False

        def write(self, *args, **kwargs):
            raise rasterio.errors.RasterioIOError("No space left on device")

    def flaky_open(fp, mode="r", *args, **kwargs):
        ds = real_open(fp, mode, *args, **kwargs)
        if mode == "w" and Path(fp).name.startswith("dem.") :
            return _FailingWriter(ds)   # the header is on disk; the data never arrives
        return ds

    with monkeypatch.context() as m:
        m.setattr(rasterio, "open", flaky_open)
        with pytest.raises(rasterio.errors.RasterioIOError):
            ingest_dem(cfg)
    assert not (cfg.paths.processed / "dem.tif").exists()
    assert not list(cfg.paths.processed.glob("*.tif"))   # no partial, no dem_reproj.tif

    out = ingest_dem(cfg)
    assert _sample(out, XMIN + 100, YMIN + 100) == 10.0


def test_dem_reproj_intermediate_removed_when_clip_fails(tmp_path, monkeypatch):
    import rasterio.mask

    cfg = _cfg(tmp_path)
    _write_tile(cfg.paths.raw / "hrdem" / "tile.tif", FULL, 10.0)

    def boom(*args, **kwargs):
        raise ValueError("clip failed")

    monkeypatch.setattr(rasterio.mask, "mask", boom)
    with pytest.raises(ValueError):
        ingest_dem(cfg)
    assert not (cfg.paths.processed / "dem_reproj.tif").exists()


# --- single-file path: byte-identical to the original implementation -------

def _original_ingest_dem(src_path, out_path, bbox, working_crs):
    """The pre-mosaic ingest_dem body, verbatim apart from paths."""
    from rasterio.mask import mask as rasterio_mask
    from rasterio.warp import Resampling, calculate_default_transform, reproject

    bbox_geom = box(*bbox)
    reproj_path = out_path.with_name("ref_reproj.tif")
    with rasterio.open(src_path) as src:
        transform, width, height = calculate_default_transform(
            src.crs, working_crs, src.width, src.height, *src.bounds
        )
        kwargs = src.meta.copy()
        kwargs.update(crs=working_crs, transform=transform, width=width, height=height)
        with rasterio.open(reproj_path, "w", **kwargs) as dst:
            for i in range(1, src.count + 1):
                reproject(
                    source=rasterio.band(src, i), destination=rasterio.band(dst, i),
                    src_transform=src.transform, src_crs=src.crs,
                    dst_transform=transform, dst_crs=working_crs,
                    resampling=Resampling.bilinear,
                )
    with rasterio.open(reproj_path) as src:
        out_image, out_transform = rasterio_mask(src, [bbox_geom], crop=True)
        out_meta = src.meta.copy()
        out_meta.update(height=out_image.shape[1], width=out_image.shape[2], transform=out_transform)
    out_meta.update(compress="lzw")
    with rasterio.open(out_path, "w", **out_meta) as dst:
        dst.write(out_image)
    reproj_path.unlink()
    return out_path


def _assert_same_raster(a, b):
    with rasterio.open(a) as ra, rasterio.open(b) as rb:
        assert ra.profile == rb.profile
        np.testing.assert_array_equal(ra.read(), rb.read())


@pytest.mark.parametrize("bounds,crs,source", [
    (FULL, CRS, "hrdem"),               # covers the study area
    (FULL, "EPSG:26920", "dem"),        # needs a real reprojection
    (WEST, CRS, "hrdem"),               # partial, nothing to fill from
])
def test_single_file_output_is_unchanged(tmp_path, bounds, crs, source):
    cfg = _cfg(tmp_path)
    name = "tile.tif" if source == "hrdem" else "cdem_tile.tif"
    tile = _write_tile(cfg.paths.raw / source / name, bounds, None, crs=crs)
    ref = _original_ingest_dem(tile, tmp_path / "ref.tif", BBOX, CRS)

    out = ingest_dem(cfg)
    _assert_same_raster(out, ref)

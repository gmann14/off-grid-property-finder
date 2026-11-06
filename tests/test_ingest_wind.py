"""Tests for ingest_wind — mocks the /vsicurl/ GWA fetches with local fixture
rasters (one per GWA layer height) so this runs without network access."""

import logging
import math
from unittest.mock import patch

import numpy as np
import pytest
import rasterio
from pyproj import Transformer
from rasterio.transform import from_bounds

from src.config import Config, Paths, StudyArea, WindSettings
from src.constants import (
    WIND_GWA_LAYERS,
    WIND_GWA_URL_TEMPLATE,
    WIND_MIN_LAYER_RATIO,
    WIND_NODATA,
    WIND_SHEAR_ALPHA_MAX,
    WIND_SHEAR_ALPHA_MIN,
)
from src.ingest import ingest_wind, wind_grid, wind_speed_at_hub
from src.manifest import layer_is_current, record_layer
from src.scoring.wind import wind_uses_proxy

CRS = "EPSG:2961"
BBOX = (380000.0, 4900000.0, 381000.0, 4901000.0)  # ~1km study area, Lunenburg-ish
URL50, URL100 = WIND_GWA_LAYERS[50], WIND_GWA_LAYERS[100]
URL10 = WIND_GWA_URL_TEMPLATE.format(height=10)
FIXTURE_BOUNDS = (-64.8, 44.1, -64.2, 44.4)  # EPSG:4326, tight around BBOX


def _make_gwa_fixture(path, value, nodata=-9999.0, bounds=FIXTURE_BOUNDS, size=(300, 300)):
    """A small EPSG:4326 raster standing in for a Global Wind Atlas Canada COG
    (also EPSG:4326). ``value`` is a constant, or a function of (lon, lat)
    arrays at pixel centres. Resolution matters: too coarse relative to the
    ~1km test bbox and the windowed read rounds to 0 pixels."""
    w, h = size
    transform = from_bounds(*bounds, w, h)
    if callable(value):
        cols, rows = np.meshgrid(np.arange(w) + 0.5, np.arange(h) + 0.5)
        lon, lat = rasterio.transform.xy(transform, rows.ravel(), cols.ravel(), offset="ul")
        lon = np.asarray(lon).reshape(h, w)
        lat = np.asarray(lat).reshape(h, w)
        data = np.asarray(value(lon, lat), dtype="float32")
    else:
        data = np.full((h, w), value, dtype="float32")
    with rasterio.open(
        path, "w", driver="GTiff", height=h, width=w, count=1,
        dtype="float32", crs="EPSG:4326", transform=transform, nodata=nodata,
    ) as dst:
        dst.write(data, 1)


def _fake_rasterio_open(fixtures: dict, opened: list | None = None):
    """Route each /vsicurl/<url> open to the local fixture for that url; a url
    with no fixture raises like a network timeout."""
    real_open = rasterio.open

    def _opener(path, *args, **kwargs):
        if isinstance(path, str) and path.startswith("/vsicurl/"):
            url = path[len("/vsicurl/"):]
            if opened is not None:
                opened.append(url)
            if url not in fixtures:
                raise ConnectionError(f"timed out fetching {url}")
            return real_open(fixtures[url])
        return real_open(path, *args, **kwargs)

    return _opener


def _fixtures(tmp_path, v50, v100, kw50=None, kw100=None):
    f50, f100 = tmp_path / "fake_gwa_50m.tif", tmp_path / "fake_gwa_100m.tif"
    _make_gwa_fixture(f50, v50, **(kw50 or {}))
    _make_gwa_fixture(f100, v100, **(kw100 or {}))
    return {URL50: f50, URL100: f100}


def _cfg(tmp_path, hub=30.0, bbox=BBOX, layers=None):
    wind = WindSettings(hub_height_m=hub) if layers is None else WindSettings(hub_height_m=hub, gwa_layers=layers)
    return Config(
        study_area=StudyArea(bbox=bbox, name="test"),
        paths=Paths(raw=tmp_path / "raw", processed=tmp_path / "processed", output=tmp_path / "out"),
        wind=wind,
    )


def _ingest(cfg, fixtures, opened=None):
    with patch("rasterio.open", side_effect=_fake_rasterio_open(fixtures, opened)):
        return ingest_wind(cfg)


def _read(out):
    with rasterio.open(out) as src:
        return src.read(1), src.nodata, src.crs.to_string(), src.tags(), src.transform


def _alpha(v_lo, v_hi, h_lo=50, h_hi=100):
    return math.log(v_hi / v_lo) / math.log(h_hi / h_lo)


def _valid(data, nodata):
    return data[data != nodata]


# --- wind_speed_at_hub (pure numpy) ----------------------------------------

def test_hub_speed_extrapolates_down_from_50m():
    # v50=5, v100=6 -> alpha = ln(1.2)/ln(2) ~ 0.263, no clamp
    v, stats = wind_speed_at_hub(np.array([5.0]), np.array([6.0]), 50, 100, 30)
    assert v[0] == pytest.approx(5.0 * 0.6 ** _alpha(5.0, 6.0), rel=1e-5)
    assert v[0] == pytest.approx(4.372, abs=1e-3)
    assert stats == {"valid": 1, "invalid": 0, "rejected_ratio": 0, "clamped_low": 0, "clamped_high": 0}


def test_hub_speed_anchor_choice_is_irrelevant_when_unclamped():
    # The fitted profile passes through both layers, so either anchor agrees.
    v50, v100 = np.array([3.0, 6.5]), np.array([4.2, 7.4])
    v, _ = wind_speed_at_hub(v50, v100, 50, 100, 30)
    a = np.log(v100 / v50) / np.log(2)
    np.testing.assert_allclose(v, v50 * 0.6 ** a, rtol=1e-5)
    np.testing.assert_allclose(v, v100 * 0.3 ** a, rtol=1e-5)


@pytest.mark.parametrize("v50, v100", [(5.0, 6.0), (4.0, 7.0), (6.0, 5.8)])  # unclamped, clamped high, clamped low
def test_hub_speed_reproduces_each_layer_at_its_own_height(v50, v100):
    # Holds even when alpha is clamped: the profile is anchored on the nearer layer.
    lo, hi = np.array([v50]), np.array([v100])
    assert wind_speed_at_hub(lo, hi, 50, 100, 50)[0][0] == pytest.approx(v50, rel=1e-6)
    assert wind_speed_at_hub(lo, hi, 50, 100, 100)[0][0] == pytest.approx(v100, rel=1e-6)


@pytest.mark.parametrize("hub, anchor_v, ratio", [
    (20.0, 5.0, 20 / 50),     # below 30 m: still anchored on 50 m
    (30.0, 5.0, 30 / 50),
    (70.0, 5.0, 70 / 50),     # between: nearer 50 m in log-height (70/50 < 100/70)
    (75.0, 6.0, 75 / 100),    # between: nearer 100 m
    (120.0, 6.0, 120 / 100),  # above 100 m: anchored on 100 m
])
def test_hub_speed_at_other_hub_heights(hub, anchor_v, ratio):
    v, _ = wind_speed_at_hub(np.array([5.0]), np.array([6.0]), 50, 100, hub)
    assert v[0] == pytest.approx(anchor_v * ratio ** _alpha(5.0, 6.0), rel=1e-5)


def test_hub_speed_clamps_high_shear():
    # v50=4, v100=7: alpha ~0.81 > 0.6, but the ratio 0.57 passes the canopy guard.
    v, stats = wind_speed_at_hub(np.array([4.0]), np.array([7.0]), 50, 100, 30)
    assert _alpha(4.0, 7.0) > WIND_SHEAR_ALPHA_MAX
    assert v[0] == pytest.approx(4.0 * 0.6 ** WIND_SHEAR_ALPHA_MAX, rel=1e-5)
    assert v[0] == pytest.approx(2.944, abs=1e-3)
    assert stats["clamped_high"] == 1 and stats["clamped_low"] == 0


def test_hub_speed_clamps_negative_shear_low():
    # v100 < v50 (raw alpha < 0, e.g. a coastal/model artefact) -> alpha_min.
    v, stats = wind_speed_at_hub(np.array([6.0]), np.array([5.8]), 50, 100, 30)
    assert _alpha(6.0, 5.8) < 0
    assert v[0] == pytest.approx(6.0 * 0.6 ** WIND_SHEAR_ALPHA_MIN, rel=1e-5)
    assert v[0] == pytest.approx(5.848, abs=1e-3)
    assert stats["clamped_low"] == 1


def test_canopy_guard_rejects_collapsed_lower_layer():
    # GWA 10 m over forest: ~0.4 under ~5 m/s at 50 m. Never fit a profile through it.
    v, stats = wind_speed_at_hub(np.array([0.4, 4.0]), np.array([5.0, 5.0]), 10, 50, 30)
    assert v[0] == WIND_NODATA
    assert v[1] != WIND_NODATA
    assert stats["rejected_ratio"] == 1 and stats["valid"] == 1 and stats["invalid"] == 1


def test_canopy_guard_boundary_is_inclusive():
    # ratio exactly 0.5 is kept (alpha = 1 -> clamped to 0.6); just below is rejected.
    v, stats = wind_speed_at_hub(np.array([3.0, 2.999]), np.array([6.0, 6.0]), 50, 100, 30)
    assert WIND_MIN_LAYER_RATIO == 0.5
    assert v[0] == pytest.approx(3.0 * 0.6 ** WIND_SHEAR_ALPHA_MAX, rel=1e-5)
    assert v[1] == WIND_NODATA
    assert stats["rejected_ratio"] == 1


def test_hub_speed_invalid_pixels_get_nodata_and_never_nan_or_inf():
    v50 = np.array([np.nan, 0.0, -1.0, 5.0, np.inf, 5.0, 5.0])
    v100 = np.array([6.0, 6.0, 6.0, np.nan, 6.0, 0.0, -np.inf])
    v, stats = wind_speed_at_hub(v50, v100, 50, 100, 30)
    assert np.isfinite(v).all()
    assert (v == WIND_NODATA).all()
    assert stats["valid"] == 0 and stats["invalid"] == 7 and stats["rejected_ratio"] == 0


def test_hub_speed_always_within_the_v50_extrapolation_bounds():
    # Property check over random layer pairs, including negative and extreme shear:
    # every valid 30 m value lies in [v50 * 0.6**alpha_max, v50 * 0.6**alpha_min].
    rng = np.random.default_rng(7)
    v50 = rng.uniform(0.2, 14.0, 20000)
    v100 = v50 * rng.uniform(0.4, 2.5, 20000)
    v, _ = wind_speed_at_hub(v50, v100, 50, 100, 30)
    ok = v != WIND_NODATA
    assert ok.sum() > 15000
    lo = v50 * 0.6 ** WIND_SHEAR_ALPHA_MAX
    hi = v50 * 0.6 ** WIND_SHEAR_ALPHA_MIN
    assert (v[ok] >= lo[ok] * (1 - 1e-6)).all() and (v[ok] <= hi[ok] * (1 + 1e-6)).all()
    assert np.isfinite(v).all()


def test_hub_speed_rejects_bad_heights():
    with pytest.raises(ValueError):
        wind_speed_at_hub(np.array([4.0]), np.array([5.0]), 100, 50, 30)
    with pytest.raises(ValueError):
        wind_speed_at_hub(np.array([4.0]), np.array([5.0]), 50, 100, 0)
    with pytest.raises(ValueError):
        wind_speed_at_hub(np.array([4.0]), np.array([4.0, 5.0]), 50, 100, 30)


# --- ingest_wind (mocked /vsicurl/) ----------------------------------------

def test_ingest_wind_writes_hub_height_raster_and_records_it(tmp_path):
    cfg = _cfg(tmp_path)
    out = _ingest(cfg, _fixtures(tmp_path, 5.0, 6.0))
    assert out is not None and out.exists()
    data, nodata, crs, tags, _ = _read(out)
    assert crs == CRS
    assert nodata == WIND_NODATA
    assert np.isfinite(data).all()
    assert (data != nodata).all()
    assert np.allclose(data, 5.0 * 0.6 ** _alpha(5.0, 6.0), atol=1e-4)  # ~4.37 m/s
    assert tags["hub_height_m"] == "30"
    assert URL50 in tags["gwa_layers"] and URL100 in tags["gwa_layers"]
    assert layer_is_current(cfg, "wind")
    assert not list(cfg.paths.processed.glob("*.partial.tif"))


@pytest.mark.parametrize("hub, expected", [
    (50.0, 5.0),                                  # reproduces the 50 m layer
    (100.0, 6.0),                                 # reproduces the 100 m layer
    (20.0, 5.0 * 0.4 ** math.log(1.2, 2)),        # below 30 m
    (120.0, 6.0 * 1.2 ** math.log(1.2, 2)),       # above 100 m
])
def test_ingest_wind_honours_configured_hub_height(tmp_path, hub, expected):
    cfg = _cfg(tmp_path, hub=hub)
    data, nodata, _, tags, _ = _read(_ingest(cfg, _fixtures(tmp_path, 5.0, 6.0)))
    assert np.allclose(_valid(data, nodata), expected, atol=1e-4)
    assert tags["hub_height_m"] == f"{hub:g}"


# Source grids coarser than the 250 m cell (~400 x 440 m and ~480 x 500 m), so
# GDAL's bilinear stays the plain 2x2 kernel, which reproduces a plane exactly.
# (Downsampling widens the kernel, which smooths a plane slightly: ~0.005 m/s.)
# The two layers sit on different grids: extent, size and resolution all differ.
COARSE_50 = {"bounds": FIXTURE_BOUNDS, "size": (120, 75)}                     # 0.005 x 0.004 deg
COARSE_100 = {"bounds": (-64.83, 44.07, -64.17, 44.43), "size": (110, 80)}    # 0.006 x 0.0045 deg


def _gradient_v50(lon, lat):
    # A plane in lon/lat (steep, so a one-pixel georeferencing slip is ~0.07 m/s).
    return 4.0 + 20.0 * (lon - FIXTURE_BOUNDS[0]) / 0.6 + 10.0 * (lat - FIXTURE_BOUNDS[1]) / 0.3


def _expected_at_cells(cfg, factor):
    transform, (h, w) = wind_grid(cfg)
    cols, rows = np.meshgrid(np.arange(w), np.arange(h))
    xs, ys = rasterio.transform.xy(transform, rows.ravel(), cols.ravel())
    lon, lat = Transformer.from_crs(CRS, "EPSG:4326", always_xy=True).transform(xs, ys)
    return (_gradient_v50(np.asarray(lon), np.asarray(lat)) * factor).reshape(h, w)


def test_ingest_wind_georeferencing_is_exact_on_mismatched_source_grids(tmp_path):
    """Regression: the source window was read with floored offsets but
    georeferenced with the fractional ones, shifting wind.tif up to a GWA
    pixel (~190 m on the real data). The 50 m and 100 m layers sit on
    different grids. Bilinear resampling of a plane is exact, so every cell
    must match the analytic value at its own centre; a one-pixel slip here is
    ~0.17 m/s."""
    ratio = 1.2  # constant shear, alpha ~0.263
    fx = _fixtures(
        tmp_path, _gradient_v50, lambda lon, lat: ratio * _gradient_v50(lon, lat),
        kw50=COARSE_50,
        kw100=COARSE_100,
    )
    cfg = _cfg(tmp_path)
    data, nodata, _, _, _ = _read(_ingest(cfg, fx))
    expected = _expected_at_cells(cfg, 0.6 ** math.log(ratio, 2))
    assert (data != nodata).all()
    np.testing.assert_allclose(data, expected, atol=2e-3)


def test_ingest_wind_bbox_partly_outside_coverage(tmp_path):
    """Regression: a window running off the dataset was silently clipped on
    read while keeping the unclipped transform. Cells outside coverage must be
    nodata, and cells inside must still be georeferenced exactly."""
    bbox = (376000.0, 4900000.0, 386000.0, 4904000.0)  # ~10 km wide
    cfg = _cfg(tmp_path, bbox=bbox)
    # Coverage ends at lon -64.5, which cuts through the study area.
    part = {"bounds": (-64.8, 44.1, -64.5, 44.4), "size": (60, 75)}  # 0.005 x 0.004 deg
    fx = _fixtures(tmp_path, _gradient_v50, lambda lon, lat: 1.2 * _gradient_v50(lon, lat), kw50=part, kw100=part)
    data, nodata, _, _, transform = _read(_ingest(cfg, fx))
    expected = _expected_at_cells(cfg, 0.6 ** math.log(1.2, 2))
    _, (h, w) = wind_grid(cfg)
    cols, rows = np.meshgrid(np.arange(w), np.arange(h))
    xs, ys = rasterio.transform.xy(transform, rows.ravel(), cols.ravel())
    lon = np.asarray(Transformer.from_crs(CRS, "EPSG:4326", always_xy=True).transform(xs, ys)[0]).reshape(h, w)
    inside = lon < -64.5 - 0.003   # a pixel+ clear of the coverage edge
    outside = lon > -64.5 + 0.003
    assert inside.any() and outside.any()
    assert (data[outside] == nodata).all()
    np.testing.assert_allclose(data[inside], expected[inside], atol=2e-3)
    assert np.isfinite(data).all()


def test_ingest_wind_bbox_entirely_outside_coverage(tmp_path, caplog):
    cfg = _cfg(tmp_path, bbox=(600000.0, 4900000.0, 601000.0, 4901000.0))  # far east of the fixture
    with caplog.at_level(logging.ERROR, logger="property_finder"):
        out = _ingest(cfg, _fixtures(tmp_path, 5.0, 6.0))
    assert out is None
    assert not (cfg.paths.processed / "wind.tif").exists()
    assert "outside the coverage" in caplog.text


def test_ingest_wind_refuses_a_canopy_collapsed_layer_pair(tmp_path, caplog):
    # The 10 m + 50 m pair over forest: 10 m ~0.4 m/s under 5 m/s.
    f10, f50 = tmp_path / "g10.tif", tmp_path / "g50.tif"
    _make_gwa_fixture(f10, 0.4)
    _make_gwa_fixture(f50, 5.0)
    cfg = _cfg(tmp_path, layers={10: URL10, 50: URL50})
    with caplog.at_level(logging.ERROR, logger="property_finder"):
        out = _ingest(cfg, {URL10: f10, URL50: f50})
    assert out is None
    assert not (cfg.paths.processed / "wind.tif").exists()
    assert "canopy" in caplog.text


def test_ingest_wind_masks_a_few_canopy_pixels_but_still_writes(tmp_path):
    # A ~1.6 x 1.3 km patch (well under WIND_MAX_REJECTED_FRACTION of the
    # 80 x 40 cells) where the lower layer collapses -> those cells nodata, the
    # rest written. The guard runs on the 250 m grid, after resampling, so a
    # sub-cell patch would be averaged away; this one spans several cells.
    def v50(lon, lat):
        patch = (lon > -64.49) & (lon < -64.47) & (lat > 44.264) & (lat < 44.276)
        return np.where(patch, 1.0, 5.0)
    fx = _fixtures(tmp_path, v50, 6.0, kw50=COARSE_50, kw100=COARSE_100)
    cfg = _cfg(tmp_path, bbox=(372000.0, 4898000.0, 392000.0, 4908000.0))
    data, nodata, _, _, _ = _read(_ingest(cfg, fx))
    n_bad = int((data == nodata).sum())
    assert 4 <= n_bad < 0.05 * data.size
    assert np.isfinite(data).all()
    good = _valid(data, nodata)
    # Cells clear of the patch get the clean value; cells on its blurred edge
    # (ratio between 0.5 and 5/6) get a lower but still guarded value.
    assert np.isclose(good, 5.0 * 0.6 ** _alpha(5.0, 6.0), atol=1e-3).mean() > 0.95
    assert (good >= 0.5 * 6.0 * 0.6 ** WIND_SHEAR_ALPHA_MAX - 1e-3).all()


def test_ingest_wind_all_nodata_layer_yields_none(tmp_path):
    # Every 50 m pixel is the source nodata value -> nothing valid -> no file
    # (an all-nodata wind.tif would silently clear the proxy flag).
    def all_nodata(lon, lat):
        return np.full(lon.shape, -9999.0)
    cfg = _cfg(tmp_path)
    assert _ingest(cfg, _fixtures(tmp_path, all_nodata, 6.0)) is None
    assert not (cfg.paths.processed / "wind.tif").exists()


def test_ingest_wind_zero_speed_layer_yields_none(tmp_path):
    cfg = _cfg(tmp_path)
    assert _ingest(cfg, _fixtures(tmp_path, 0.0, 6.0, kw50={"nodata": None})) is None
    assert not (cfg.paths.processed / "wind.tif").exists()


def test_ingest_wind_negative_shear_clamps_low(tmp_path):
    cfg = _cfg(tmp_path)
    data, nodata, _, _, _ = _read(_ingest(cfg, _fixtures(tmp_path, 6.0, 5.8)))
    assert np.allclose(_valid(data, nodata), 6.0 * 0.6 ** WIND_SHEAR_ALPHA_MIN, atol=1e-4)


def test_ingest_wind_clears_the_proxy_flag(tmp_path):
    cfg = _cfg(tmp_path)
    assert wind_uses_proxy(cfg) is True  # no wind.tif yet
    _ingest(cfg, _fixtures(tmp_path, 5.0, 6.0))
    assert wind_uses_proxy(cfg) is False


def test_ingest_wind_rerun_is_a_noop(tmp_path):
    cfg = _cfg(tmp_path)
    fx = _fixtures(tmp_path, 5.0, 6.0)
    out = _ingest(cfg, fx)
    assert out is not None
    before = (out.read_bytes(), out.stat().st_mtime_ns, (cfg.paths.processed / ".manifest.json").read_bytes())
    opened = []
    assert _ingest(cfg, fx, opened) == out
    assert opened == []  # no network on a current wind.tif
    assert (out.read_bytes(), out.stat().st_mtime_ns, (cfg.paths.processed / ".manifest.json").read_bytes()) == before


def test_ingest_wind_skips_current_file_without_network(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.paths.processed.mkdir(parents=True)
    sentinel = cfg.paths.processed / "wind.tif"
    sentinel.write_bytes(b"already here")
    record_layer(cfg, "wind")
    with patch("rasterio.open") as mock_open:
        out = ingest_wind(cfg)
    mock_open.assert_not_called()
    assert out == sentinel


def test_ingest_wind_rebuilds_a_stale_file_in_place(tmp_path):
    old_cfg = _cfg(tmp_path, hub=50.0)
    out = _ingest(old_cfg, _fixtures(tmp_path, 5.0, 6.0))
    assert out is not None
    new_cfg = _cfg(tmp_path, hub=30.0)
    assert not layer_is_current(new_cfg, "wind")
    assert _ingest(new_cfg, _fixtures(tmp_path, 5.0, 6.0)) == out
    data, nodata, _, tags, _ = _read(out)
    assert tags["hub_height_m"] == "30"
    assert np.allclose(_valid(data, nodata), 5.0 * 0.6 ** _alpha(5.0, 6.0), atol=1e-4)
    assert layer_is_current(new_cfg, "wind")


def test_second_layer_failure_keeps_previous_file_and_record(tmp_path, caplog):
    """The 50 m read succeeds, then the 100 m read times out: no half-written
    wind.tif, the previous file and its manifest record untouched, clear error."""
    old_cfg = _cfg(tmp_path, hub=50.0)
    out = _ingest(old_cfg, _fixtures(tmp_path, 5.0, 6.0))
    assert out is not None
    old_bytes = out.read_bytes()
    old_manifest = (tmp_path / "processed" / ".manifest.json").read_bytes()

    fx = _fixtures(tmp_path, 5.0, 6.0)
    del fx[URL100]
    opened = []
    with caplog.at_level(logging.ERROR, logger="property_finder"):
        result = _ingest(_cfg(tmp_path, hub=30.0), fx, opened)
    assert result is None
    assert opened == [URL50, URL100]  # the first layer really was read
    assert out.read_bytes() == old_bytes
    assert (tmp_path / "processed" / ".manifest.json").read_bytes() == old_manifest
    assert not list((tmp_path / "processed").glob("*.partial.tif"))
    assert "100m" in caplog.text and "timed out" in caplog.text and "unchanged" in caplog.text


def test_second_layer_failure_on_a_fresh_directory_writes_nothing(tmp_path):
    fx = _fixtures(tmp_path, 5.0, 6.0)
    del fx[URL100]
    cfg = _cfg(tmp_path)
    assert _ingest(cfg, fx) is None
    assert not (cfg.paths.processed / "wind.tif").exists()
    assert not list(cfg.paths.processed.glob("*.partial.tif"))
    assert not layer_is_current(cfg, "wind")


def test_failed_write_is_atomic(tmp_path):
    """A crash at the rename step leaves the previous wind.tif and its record
    exactly as they were, and no partial file behind."""
    old_cfg = _cfg(tmp_path, hub=50.0)
    out = _ingest(old_cfg, _fixtures(tmp_path, 5.0, 6.0))
    assert out is not None
    old_bytes = out.read_bytes()
    with patch("os.replace", side_effect=OSError("disk full")):
        with pytest.raises(OSError, match="disk full"):
            _ingest(_cfg(tmp_path, hub=30.0), _fixtures(tmp_path, 5.0, 6.0))
    assert out.read_bytes() == old_bytes
    assert layer_is_current(old_cfg, "wind")
    assert not list((tmp_path / "processed").glob("*.partial.tif"))


def test_fetch_failure_returns_none(tmp_path):
    cfg = _cfg(tmp_path)
    with patch("rasterio.open", side_effect=ConnectionError("timed out")):
        assert ingest_wind(cfg) is None
    assert not (cfg.paths.processed / "wind.tif").exists()


def test_gwa_reads_use_bounded_http_timeouts(tmp_path):
    seen = {}
    real_env = rasterio.Env

    def _env(**kw):
        seen.update(kw)
        return real_env(**kw)

    with patch("rasterio.Env", side_effect=_env):
        _ingest(_cfg(tmp_path), _fixtures(tmp_path, 5.0, 6.0))
    assert int(seen["GDAL_HTTP_TIMEOUT"]) > 0
    assert int(seen["GDAL_HTTP_MAX_RETRY"]) >= 1


@pytest.mark.parametrize("bbox", [
    (380000.0, 4900000.0, 381000.0, 4901000.0),   # exact multiple of 250 m
    (380000.0, 4900000.0, 381100.0, 4900900.0),   # not a multiple: partial last row/col
])
def test_wind_grid_matches_candidate_grid_cells(tmp_path, bbox):
    """Regression: the old grid used int(width / res) pixels stretched over the
    bbox, so a bbox that isn't a multiple of 250 m gave non-square pixels that
    drift off the candidate cells. Each candidate cell must map to exactly one
    wind pixel, centre on centre."""
    from src.grid import generate_candidate_grid

    cfg = _cfg(tmp_path, bbox=bbox)
    transform, (h, w) = wind_grid(cfg)
    assert transform.a == 250.0 and transform.e == -250.0
    grid = generate_candidate_grid(bbox, cell_size=250, crs=CRS)
    assert len(grid) == h * w
    cx, cy = grid.geometry.centroid.x.values, grid.geometry.centroid.y.values
    rows, cols = rasterio.transform.rowcol(transform, cx, cy)
    px, py = rasterio.transform.xy(transform, rows, cols)
    np.testing.assert_allclose(px, cx)
    np.testing.assert_allclose(py, cy)
    assert len(set(zip(rows, cols))) == len(grid)

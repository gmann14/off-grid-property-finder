"""Tests for the processed-data manifest (stale-cache guard)."""

import json

import pytest

from src.config import Config, Paths, StudyArea
from src.manifest import (
    MANIFEST_FILENAME,
    StaleProcessedDataError,
    check_and_update_manifest,
    layer_is_current,
    record_layer,
    verify_manifest_fresh,
)

BBOX = (360000.0, 4880000.0, 410000.0, 4930000.0)


def _cfg(tmp_path, **overrides):
    kwargs = dict(
        study_area=StudyArea(bbox=BBOX, name="lunenburg"),
        cell_size_m=250,
        paths=Paths(raw=tmp_path / "raw", processed=tmp_path / "processed", output=tmp_path / "out"),
    )
    kwargs.update(overrides)
    return Config(**kwargs)


# --- check_and_update_manifest (write-side, used by ingest/prepare) --------

def test_no_manifest_is_baselined_not_blocked(tmp_path):
    """A missing manifest (e.g. a pre-existing data/processed/) must not be
    treated as stale — it's baselined against the current config and allowed
    to proceed. This is the safety property that protects real existing data."""
    cfg = _cfg(tmp_path)
    check_and_update_manifest(cfg)  # must not raise
    manifest_path = cfg.paths.processed / MANIFEST_FILENAME
    assert manifest_path.exists()
    data = json.loads(manifest_path.read_text())
    assert data["study_area_name"] == "lunenburg"
    assert data["cell_size_m"] == 250


def test_matching_config_is_a_noop(tmp_path):
    cfg = _cfg(tmp_path)
    check_and_update_manifest(cfg)  # baseline
    check_and_update_manifest(cfg)  # should not raise or change anything


def test_mismatch_raises_without_force(tmp_path):
    cfg = _cfg(tmp_path)
    check_and_update_manifest(cfg)  # baseline at cell_size_m=250

    changed = _cfg(tmp_path, cell_size_m=500)
    with pytest.raises(StaleProcessedDataError, match="cell_size_m"):
        check_and_update_manifest(changed)


def test_mismatch_does_not_delete_anything_without_force(tmp_path):
    """Nothing is ever deleted without an explicit --force."""
    cfg = _cfg(tmp_path)
    check_and_update_manifest(cfg)
    real_output = cfg.paths.processed / "dem.tif"
    real_output.write_bytes(b"not a real geotiff, just a sentinel")

    changed = _cfg(tmp_path, cell_size_m=500)
    with pytest.raises(StaleProcessedDataError):
        check_and_update_manifest(changed)
    assert real_output.exists()  # untouched


def test_force_deletes_generated_files_and_rebaselines(tmp_path):
    cfg = _cfg(tmp_path)
    check_and_update_manifest(cfg)
    (cfg.paths.processed / "dem.tif").write_bytes(b"stale raster")
    (cfg.paths.processed / "parcels.gpkg").write_bytes(b"stale vector")
    (cfg.paths.processed / "notes.txt").write_text("not a generated output")

    changed = _cfg(tmp_path, cell_size_m=500)
    check_and_update_manifest(changed, force=True)  # must not raise

    assert not (cfg.paths.processed / "dem.tif").exists()
    assert not (cfg.paths.processed / "parcels.gpkg").exists()
    assert (cfg.paths.processed / "notes.txt").exists()  # not a tracked pattern

    # Manifest is rebaselined to the new config — a second call is a no-op.
    check_and_update_manifest(changed, force=False)


def test_bbox_change_detected(tmp_path):
    cfg = _cfg(tmp_path)
    check_and_update_manifest(cfg)
    changed = _cfg(tmp_path, study_area=StudyArea(bbox=(0, 0, 1000, 1000), name="lunenburg"))
    with pytest.raises(StaleProcessedDataError, match="study_area_bbox"):
        check_and_update_manifest(changed)


def test_working_crs_change_detected(tmp_path):
    cfg = _cfg(tmp_path)
    check_and_update_manifest(cfg)
    changed = _cfg(tmp_path, working_crs="EPSG:3857")
    with pytest.raises(StaleProcessedDataError, match="working_crs"):
        check_and_update_manifest(changed)


def test_unreadable_manifest_treated_as_missing(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.paths.processed.mkdir(parents=True)
    (cfg.paths.processed / MANIFEST_FILENAME).write_text("{not valid json")
    check_and_update_manifest(cfg)  # must not raise — rebaselines instead


# --- verify_manifest_fresh (read-only, used by score) -----------------------

def test_verify_fresh_noop_when_no_manifest(tmp_path):
    cfg = _cfg(tmp_path)
    verify_manifest_fresh(cfg)  # must not raise — no manifest is not an error


def test_verify_fresh_noop_when_matching(tmp_path):
    cfg = _cfg(tmp_path)
    check_and_update_manifest(cfg)
    verify_manifest_fresh(cfg)  # must not raise


def test_verify_fresh_raises_on_mismatch(tmp_path):
    cfg = _cfg(tmp_path)
    check_and_update_manifest(cfg)
    changed = _cfg(tmp_path, cell_size_m=500)
    with pytest.raises(StaleProcessedDataError):
        verify_manifest_fresh(changed)


# --- layer-scoped records (wind.tif: GWA source + hub height) ---------------

def _wind_cfg(tmp_path, hub=30.0, layers=None, **overrides):
    from src.config import WindSettings
    kw = {"hub_height_m": hub}
    if layers is not None:
        kw["gwa_layers"] = layers
    return _cfg(tmp_path, wind=WindSettings(**kw), **overrides)


def _put(cfg, name, payload=b"x"):
    path = cfg.paths.processed / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def _manifest(cfg):
    return json.loads((cfg.paths.processed / MANIFEST_FILENAME).read_text())


_LEGACY = {  # a manifest from before layer tracking (the real July one)
    "study_area_name": "lunenburg", "study_area_bbox": list(BBOX),
    "cell_size_m": 250, "working_crs": "EPSG:2961",
}


def test_record_layer_records_wind_source_hub_height_and_method(tmp_path):
    from src.constants import WIND_GWA_LAYERS, WIND_INGEST_METHOD
    cfg = _wind_cfg(tmp_path)
    check_and_update_manifest(cfg)
    assert _manifest(cfg)["layers"] == {}  # the check itself records nothing
    assert not layer_is_current(cfg, "wind")
    record_layer(cfg, "wind")
    wind = _manifest(cfg)["layers"]["wind"]
    assert wind == {
        "method": WIND_INGEST_METHOD,
        "hub_height_m": 30.0,
        "gwa_layers": {str(h): u for h, u in WIND_GWA_LAYERS.items()},
    }
    assert sorted(wind["gwa_layers"]) == ["100", "50"]
    assert layer_is_current(cfg, "wind")


def test_record_layer_without_a_manifest_baselines_it(tmp_path):
    cfg = _wind_cfg(tmp_path)
    record_layer(cfg, "wind")
    data = _manifest(cfg)
    assert data["cell_size_m"] == 250 and "wind" in data["layers"]


def test_record_layer_never_rewrites_the_global_fields(tmp_path):
    """Regression: recording wind after a cell-size change must not bless the
    DEM derivatives built at the old cell size."""
    check_and_update_manifest(_wind_cfg(tmp_path))  # built at 250 m
    _put(_wind_cfg(tmp_path), "dem.tif")
    changed = _wind_cfg(tmp_path, cell_size_m=500)
    record_layer(changed, "wind")
    assert _manifest(changed)["cell_size_m"] == 250
    with pytest.raises(StaleProcessedDataError, match="cell_size_m"):
        verify_manifest_fresh(changed)


@pytest.mark.parametrize("change", ["hub", "source", "method"])
def test_any_wind_input_change_makes_the_record_stale(tmp_path, monkeypatch, change):
    cfg = _wind_cfg(tmp_path)
    record_layer(cfg, "wind")
    _put(cfg, "wind.tif")
    if change == "hub":
        new, match = _wind_cfg(tmp_path, hub=20.0), "hub_height_m"
    elif change == "source":
        new, match = _wind_cfg(tmp_path, layers={50: "https://mirror/50m.tif", 100: "https://mirror/100m.tif"}), "gwa_layers"
    else:
        monkeypatch.setattr("src.manifest.WIND_INGEST_METHOD", "gwa-powerlaw-v999")
        new, match = cfg, "method"
    assert not layer_is_current(new, "wind")
    with pytest.raises(StaleProcessedDataError, match=match):
        verify_manifest_fresh(new)       # score refuses
    with pytest.raises(StaleProcessedDataError, match=match):
        check_and_update_manifest(new)   # ingest refuses without --force


def test_layer_drift_without_force_deletes_nothing(tmp_path):
    cfg = _wind_cfg(tmp_path)
    record_layer(cfg, "wind")
    wind = _put(cfg, "wind.tif", b"july wind")
    with pytest.raises(StaleProcessedDataError):
        check_and_update_manifest(_wind_cfg(tmp_path, hub=20.0))
    assert wind.read_bytes() == b"july wind"


def test_layer_drift_with_force_keeps_the_old_file_until_ingest_replaces_it(tmp_path):
    """Regression: --force used to delete wind.tif before re-fetching, so a
    failed fetch lost the previous file. Now nothing is deleted at check time,
    the record stays as it was (so score still refuses the old file), and
    only a successful rebuild + record_layer makes it fresh."""
    cfg = _wind_cfg(tmp_path, hub=30.0)
    check_and_update_manifest(cfg)
    record_layer(cfg, "wind")
    wind = _put(cfg, "wind.tif", b"old wind")
    dem = _put(cfg, "dem.tif", b"dem")
    parcels = _put(cfg, "parcels.gpkg", b"parcels")
    before = _manifest(cfg)

    changed = _wind_cfg(tmp_path, hub=20.0)
    check_and_update_manifest(changed, force=True)
    assert wind.read_bytes() == b"old wind"
    assert dem.read_bytes() == b"dem" and parcels.read_bytes() == b"parcels"
    assert _manifest(changed) == before
    assert not layer_is_current(changed, "wind")   # ingest_wind will rebuild it
    with pytest.raises(StaleProcessedDataError):
        verify_manifest_fresh(changed)             # a failed rebuild can't be scored

    record_layer(changed, "wind")                  # what a successful ingest_wind does
    verify_manifest_fresh(changed)
    check_and_update_manifest(changed)             # and a re-run is now a no-op


def test_rerunning_the_check_is_a_noop(tmp_path):
    cfg = _wind_cfg(tmp_path)
    check_and_update_manifest(cfg)
    record_layer(cfg, "wind")
    _put(cfg, "wind.tif")
    path = cfg.paths.processed / MANIFEST_FILENAME
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    check_and_update_manifest(cfg)
    check_and_update_manifest(cfg, force=True)  # --force with nothing stale deletes nothing
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert (cfg.paths.processed / "wind.tif").exists()


def test_legacy_manifest_with_unrecorded_wind_tif_is_stale(tmp_path):
    """The real July data/processed/: a manifest from before layer tracking plus
    a wind.tif built from GWA 100 m. It must not be baselined as if current."""
    cfg = _wind_cfg(tmp_path)
    _put(cfg, MANIFEST_FILENAME, json.dumps(_LEGACY).encode())
    wind = _put(cfg, "wind.tif", b"july wind")
    dem = _put(cfg, "dem.tif")

    with pytest.raises(StaleProcessedDataError, match="no record"):
        verify_manifest_fresh(cfg)  # score refuses
    with pytest.raises(StaleProcessedDataError, match="no record"):
        check_and_update_manifest(cfg)  # ingest refuses without --force

    check_and_update_manifest(cfg, force=True)
    assert wind.read_bytes() == b"july wind" and dem.exists()  # nothing deleted
    assert not layer_is_current(cfg, "wind")                  # so ingest_wind rebuilds it
    record_layer(cfg, "wind")
    data = _manifest(cfg)
    assert data["layers"]["wind"]["hub_height_m"] == 30.0
    assert {k: data[k] for k in _LEGACY} == _LEGACY            # global fields preserved
    verify_manifest_fresh(cfg)


def test_legacy_manifest_without_wind_tif_is_not_stale(tmp_path):
    cfg = _wind_cfg(tmp_path)
    _put(cfg, MANIFEST_FILENAME, json.dumps(_LEGACY).encode())
    check_and_update_manifest(cfg)  # nothing to mis-serve -> no raise
    verify_manifest_fresh(cfg)
    assert not layer_is_current(cfg, "wind")  # ingest_wind will build and record it


@pytest.mark.parametrize("layers", [None, [], "wind", {"wind": "x"}, {"wind": None}])
def test_malformed_layer_records_are_treated_as_missing(tmp_path, layers):
    cfg = _wind_cfg(tmp_path)
    _put(cfg, MANIFEST_FILENAME, json.dumps({**_LEGACY, "layers": layers}).encode())
    assert not layer_is_current(cfg, "wind")
    _put(cfg, "wind.tif")
    with pytest.raises(StaleProcessedDataError):
        verify_manifest_fresh(cfg)
    record_layer(cfg, "wind")
    verify_manifest_fresh(cfg)


@pytest.mark.parametrize("payload", ["[]", "null", '"text"', "42"])
def test_non_object_manifest_is_treated_as_missing(tmp_path, payload):
    """Regression: a manifest that parses but isn't a JSON object crashed
    with AttributeError instead of being re-baselined."""
    cfg = _wind_cfg(tmp_path)
    _put(cfg, MANIFEST_FILENAME, payload.encode())
    verify_manifest_fresh(cfg)
    check_and_update_manifest(cfg)
    assert _manifest(cfg)["cell_size_m"] == 250


def test_no_manifest_with_wind_tif_is_stale(tmp_path):
    cfg = _wind_cfg(tmp_path)
    wind = _put(cfg, "wind.tif")
    with pytest.raises(StaleProcessedDataError, match="no record"):
        check_and_update_manifest(cfg)
    check_and_update_manifest(cfg, force=True)
    assert wind.exists()
    assert _manifest(cfg)["cell_size_m"] == 250   # global fields baselined
    assert not layer_is_current(cfg, "wind")


def test_global_drift_with_force_still_clears_everything_including_wind(tmp_path):
    cfg = _wind_cfg(tmp_path)
    check_and_update_manifest(cfg)
    record_layer(cfg, "wind")
    wind = _put(cfg, "wind.tif")
    dem = _put(cfg, "dem.tif")
    changed = _wind_cfg(tmp_path, cell_size_m=500)
    check_and_update_manifest(changed, force=True)
    assert not wind.exists() and not dem.exists()
    assert _manifest(changed)["layers"] == {}   # records went with their outputs
    assert _manifest(changed)["cell_size_m"] == 500


def test_manifest_write_is_atomic(tmp_path, monkeypatch):
    """Regression: a crash mid-write used to be able to leave a truncated
    manifest, later read as 'missing' and silently re-baselined."""
    cfg = _wind_cfg(tmp_path)
    check_and_update_manifest(cfg)
    path = cfg.paths.processed / MANIFEST_FILENAME
    before = path.read_bytes()

    def _boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr("src.manifest.os.replace", _boom)
    with pytest.raises(OSError, match="disk full"):
        record_layer(cfg, "wind")
    assert path.read_bytes() == before
    assert not list(cfg.paths.processed.glob("*.tmp"))


def test_renaming_the_study_area_is_not_stale_and_deletes_nothing(tmp_path):
    cfg = _cfg(tmp_path)
    check_and_update_manifest(cfg)
    kept = cfg.paths.processed / "parcels.gpkg"
    kept.write_text("x")
    renamed = _cfg(tmp_path, study_area=StudyArea(bbox=BBOX, name="lunenburg-south"))
    verify_manifest_fresh(renamed)          # must not raise
    check_and_update_manifest(renamed)      # must not raise or clear anything
    assert kept.exists()

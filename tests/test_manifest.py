"""Tests for the processed-data manifest (stale-cache guard)."""

import json

import pytest

from src.config import Config, Paths, StudyArea
from src.manifest import (
    MANIFEST_FILENAME,
    StaleProcessedDataError,
    check_and_update_manifest,
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

def _wind_cfg(tmp_path, hub=30.0, layers=None):
    from src.config import WindSettings
    kw = {"hub_height_m": hub}
    if layers is not None:
        kw["gwa_layers"] = layers
    return _cfg(tmp_path, wind=WindSettings(**kw))


def _put(cfg, name, payload=b"x"):
    path = cfg.paths.processed / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def test_manifest_records_wind_source_and_hub_height(tmp_path):
    from src.constants import WIND_GWA_LAYERS, WIND_INGEST_METHOD
    cfg = _wind_cfg(tmp_path)
    check_and_update_manifest(cfg)
    data = json.loads((cfg.paths.processed / MANIFEST_FILENAME).read_text())
    wind = data["layers"]["wind"]
    assert wind["hub_height_m"] == 30.0
    assert wind["method"] == WIND_INGEST_METHOD
    assert wind["gwa_layers"] == {str(h): u for h, u in WIND_GWA_LAYERS.items()}


def test_hub_height_change_raises_without_force_and_keeps_wind_tif(tmp_path):
    cfg = _wind_cfg(tmp_path, hub=30.0)
    check_and_update_manifest(cfg)
    wind = _put(cfg, "wind.tif")
    with pytest.raises(StaleProcessedDataError, match="hub_height_m"):
        check_and_update_manifest(_wind_cfg(tmp_path, hub=20.0))
    assert wind.exists()


def test_hub_height_change_with_force_deletes_only_wind_tif(tmp_path):
    cfg = _wind_cfg(tmp_path, hub=30.0)
    check_and_update_manifest(cfg)
    wind = _put(cfg, "wind.tif")
    dem = _put(cfg, "dem.tif")
    parcels = _put(cfg, "parcels.gpkg")

    changed = _wind_cfg(tmp_path, hub=20.0)
    check_and_update_manifest(changed, force=True)
    assert not wind.exists()          # regenerated by ingest_wind next
    assert dem.exists() and parcels.exists()  # unrelated layers untouched
    check_and_update_manifest(changed)        # rebaselined -> no-op
    _put(cfg, "wind.tif")                     # the regenerated file
    verify_manifest_fresh(changed)            # fresh


def test_wind_source_change_is_detected(tmp_path):
    cfg = _wind_cfg(tmp_path)
    check_and_update_manifest(cfg)
    _put(cfg, "wind.tif")
    other = _wind_cfg(tmp_path, layers={10: "https://mirror/10m.tif", 50: "https://mirror/50m.tif"})
    with pytest.raises(StaleProcessedDataError, match="gwa_layers"):
        check_and_update_manifest(other)
    with pytest.raises(StaleProcessedDataError, match="gwa_layers"):
        verify_manifest_fresh(other)


def test_legacy_manifest_with_unrecorded_wind_tif_is_stale(tmp_path):
    """The real July data/processed/: a manifest from before layer tracking plus
    a wind.tif built from GWA 100 m. It must not be baselined as if current."""
    cfg = _wind_cfg(tmp_path)
    legacy = {
        "study_area_name": "lunenburg", "study_area_bbox": list(BBOX),
        "cell_size_m": 250, "working_crs": "EPSG:2961",
    }
    _put(cfg, MANIFEST_FILENAME, json.dumps(legacy).encode())
    wind = _put(cfg, "wind.tif")
    dem = _put(cfg, "dem.tif")

    with pytest.raises(StaleProcessedDataError, match="no record"):
        verify_manifest_fresh(cfg)  # score refuses
    with pytest.raises(StaleProcessedDataError, match="no record"):
        check_and_update_manifest(cfg)  # ingest refuses without --force
    assert wind.exists()

    check_and_update_manifest(cfg, force=True)
    assert not wind.exists() and dem.exists()
    data = json.loads((cfg.paths.processed / MANIFEST_FILENAME).read_text())
    assert data["layers"]["wind"]["hub_height_m"] == 30.0


def test_legacy_manifest_without_wind_tif_just_gains_the_record(tmp_path):
    cfg = _wind_cfg(tmp_path)
    legacy = {
        "study_area_name": "lunenburg", "study_area_bbox": list(BBOX),
        "cell_size_m": 250, "working_crs": "EPSG:2961",
    }
    _put(cfg, MANIFEST_FILENAME, json.dumps(legacy).encode())
    check_and_update_manifest(cfg)  # nothing to mis-serve -> no raise
    verify_manifest_fresh(cfg)
    data = json.loads((cfg.paths.processed / MANIFEST_FILENAME).read_text())
    assert "wind" in data["layers"]


def test_no_manifest_with_wind_tif_is_stale(tmp_path):
    cfg = _wind_cfg(tmp_path)
    _put(cfg, "wind.tif")
    with pytest.raises(StaleProcessedDataError):
        check_and_update_manifest(cfg)


def test_global_drift_with_force_still_clears_everything_including_wind(tmp_path):
    cfg = _wind_cfg(tmp_path)
    check_and_update_manifest(cfg)
    wind = _put(cfg, "wind.tif")
    dem = _put(cfg, "dem.tif")
    check_and_update_manifest(_cfg(tmp_path, cell_size_m=500), force=True)
    assert not wind.exists() and not dem.exists()

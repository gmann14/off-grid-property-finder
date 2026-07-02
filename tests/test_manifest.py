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

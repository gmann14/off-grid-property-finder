"""Processed-data manifest — guards against silently stale cached outputs.

Every ingest/derivative step skips regeneration when its output file already
exists, keyed on nothing but the file's presence. Changing `study_area.bbox`,
`cell_size_m`, or `working_crs` in config.yaml would otherwise silently reuse
old-region rasters/vectors with no warning. This module records the
run-defining config values alongside the processed outputs and detects when
they've drifted.

Safety note: a missing manifest (e.g. a `data/processed/` directory that
predates this feature) is treated as "unknown, not stale" — it is baselined
(the manifest is written to match the current config) rather than judged or
deleted. Only an actual, detected MISMATCH triggers the stricter behavior
(raise, or delete-and-rebuild under `--force`). Nothing is ever deleted
without an explicit `--force`.
"""

import json
import logging
from pathlib import Path

from src.config import Config

logger = logging.getLogger("property_finder")

MANIFEST_FILENAME = ".manifest.json"

# Config fields that define what a processed/ directory's outputs represent.
# If any of these change, the cached outputs no longer match the config.
_TRACKED_FIELDS = ("study_area_name", "study_area_bbox", "cell_size_m", "working_crs")

# Glob patterns for the generated files a `--force` rebuild clears. The
# manifest itself is excluded (it's rewritten separately) and raw data in
# data/raw/ is never touched.
_GENERATED_GLOBS = ("*.tif", "*.tiff", "*.gpkg", "*.geojson", "*.csv")


def _current_fields(config: Config) -> dict:
    return {
        "study_area_name": config.study_area.name,
        "study_area_bbox": list(config.study_area.bbox),
        "cell_size_m": config.cell_size_m,
        "working_crs": config.working_crs,
    }


def _manifest_path(config: Config) -> Path:
    return config.paths.processed / MANIFEST_FILENAME


def _read_manifest(config: Config) -> dict | None:
    path = _manifest_path(config)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        logger.warning("Manifest at %s is unreadable; treating as missing", path)
        return None


def _write_manifest(config: Config) -> None:
    path = _manifest_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_current_fields(config), indent=2))


def _diff(existing: dict, current: dict) -> list[str]:
    return [
        f"{field}: {existing.get(field)!r} -> {current.get(field)!r}"
        for field in _TRACKED_FIELDS
        if existing.get(field) != current.get(field)
    ]


class StaleProcessedDataError(RuntimeError):
    """Raised when data/processed/ was built from a different config."""


def check_and_update_manifest(config: Config, force: bool = False) -> None:
    """Guard for ingest/prepare entry points: detect config drift vs. the
    processed-data manifest.

    - No manifest yet -> baseline it (write current config, proceed). This is
      the case for any pre-existing data/processed/ directory; it is NOT
      treated as stale, since there is no record of what it was built from.
    - Manifest matches current config -> no-op, proceed.
    - Manifest mismatches and ``force`` is False -> raise, so a bbox/cell-size
      change never silently scores the wrong region. Nothing is deleted.
    - Manifest mismatches and ``force`` is True -> delete generated outputs
      in data/processed/ (not the raw data) and rebaseline the manifest, so
      every ingest step's skip-if-exists check naturally regenerates them.
    """
    existing = _read_manifest(config)
    current = _current_fields(config)

    if existing is None:
        logger.info(
            "No processed-data manifest found; baselining data/processed/ "
            "against the current config (bbox=%s, cell_size_m=%s).",
            current["study_area_bbox"], current["cell_size_m"],
        )
        _write_manifest(config)
        return

    diffs = _diff(existing, current)
    if not diffs:
        return

    if not force:
        raise StaleProcessedDataError(
            "data/processed/ was built from a different config and may contain "
            "stale outputs for the wrong region:\n  " + "\n  ".join(diffs) +
            "\nRun with --force to delete and regenerate the affected outputs, "
            "or point `paths.processed` at a fresh directory for this config."
        )

    logger.warning(
        "Config changed since data/processed/ was built — %s. "
        "--force: deleting generated outputs and regenerating.",
        "; ".join(diffs),
    )
    processed = config.paths.processed
    removed = 0
    for pattern in _GENERATED_GLOBS:
        for path in processed.glob(pattern):
            path.unlink()
            removed += 1
    logger.info("Removed %d stale generated file(s) from %s", removed, processed)
    _write_manifest(config)


def verify_manifest_fresh(config: Config) -> None:
    """Read-only check for entry points that consume (but don't regenerate)
    processed data, e.g. `score`/`visualize`. Raises on a detected mismatch;
    a missing manifest is not an error (legacy/pre-existing data)."""
    existing = _read_manifest(config)
    if existing is None:
        return
    diffs = _diff(existing, _current_fields(config))
    if diffs:
        raise StaleProcessedDataError(
            "data/processed/ was built from a different config than is currently "
            "active:\n  " + "\n  ".join(diffs) +
            "\nRe-run `prepare`/`ingest` (with --force if the config change is "
            "intentional) before scoring."
        )

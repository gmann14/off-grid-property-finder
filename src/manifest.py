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

Layer-scoped records: some outputs depend on inputs beyond the study area —
e.g. wind.tif depends on the GWA layers, the hub height and the ingest method.
Those are recorded per layer under ``"layers"`` and only invalidate that
layer's own outputs (a hub-height change re-fetches wind.tif; it does not wipe
the DEM derivatives). Unlike the global fields, an existing layer output with
NO record is treated as stale, not baselined: it can only have been written by
code that predates the record (for wind.tif, the old 100 m-only ingest), so
baselining it would bless exactly the stale file this guards against.
"""

import json
import logging
from pathlib import Path

from src.config import Config
from src.constants import WIND_INGEST_METHOD

logger = logging.getLogger("property_finder")

MANIFEST_FILENAME = ".manifest.json"

# Config fields that define what a processed/ directory's outputs represent.
# If any of these change, the cached outputs no longer match the config.
_TRACKED_FIELDS = ("study_area_name", "study_area_bbox", "cell_size_m", "working_crs")

# Glob patterns for the generated files a `--force` rebuild clears. The
# manifest itself is excluded (it's rewritten separately) and raw data in
# data/raw/ is never touched.
_GENERATED_GLOBS = ("*.tif", "*.tiff", "*.gpkg", "*.geojson", "*.csv")

# Per-layer outputs whose validity depends on that layer's own recorded inputs
# (see `_current_layers`). A layer drift clears only these files.
_LAYER_OUTPUTS = {"wind": ("wind.tif",)}


def _current_layers(config: Config) -> dict:
    return {
        "wind": {
            "method": WIND_INGEST_METHOD,
            "hub_height_m": float(config.wind.hub_height_m),
            # JSON object keys are strings; normalize so a round-trip compares equal.
            "gwa_layers": {str(h): url for h, url in sorted(config.wind.gwa_layers.items())},
        },
    }


def _current_fields(config: Config) -> dict:
    return {
        "study_area_name": config.study_area.name,
        "study_area_bbox": list(config.study_area.bbox),
        "cell_size_m": config.cell_size_m,
        "working_crs": config.working_crs,
    }


def _current_manifest(config: Config) -> dict:
    return {**_current_fields(config), "layers": _current_layers(config)}


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
    path.write_text(json.dumps(_current_manifest(config), indent=2))


def _diff(existing: dict, current: dict) -> list[str]:
    return [
        f"{field}: {existing.get(field)!r} -> {current.get(field)!r}"
        for field in _TRACKED_FIELDS
        if existing.get(field) != current.get(field)
    ]


def _stale_layers(existing: dict | None, config: Config) -> dict[str, str]:
    """Layers whose output exists in data/processed/ but whose recorded inputs
    are missing or differ from the config -> a human-readable reason each.
    A layer with no output on disk is never stale (nothing to mis-serve)."""
    processed = config.paths.processed
    recorded = (existing or {}).get("layers") or {}
    stale = {}
    for layer, current in _current_layers(config).items():
        if not any((processed / f).exists() for f in _LAYER_OUTPUTS[layer]):
            continue
        rec = recorded.get(layer)
        if rec is None:
            stale[layer] = (
                f"layers.{layer}: no record (built before its inputs were tracked) -> {current!r}"
            )
        elif rec != current:
            changed = sorted(k for k in set(rec) | set(current) if rec.get(k) != current.get(k))
            stale[layer] = "; ".join(
                f"layers.{layer}.{k}: {rec.get(k)!r} -> {current.get(k)!r}" for k in changed
            )
    return stale


class StaleProcessedDataError(RuntimeError):
    """Raised when data/processed/ was built from a different config."""


def check_and_update_manifest(config: Config, force: bool = False) -> None:
    """Guard for ingest/prepare entry points: detect config drift vs. the
    processed-data manifest.

    - No manifest yet -> baseline it (write current config, proceed). This is
      the case for any pre-existing data/processed/ directory; it is NOT
      treated as stale, since there is no record of what it was built from.
      (Layer outputs are the exception — see the module docstring.)
    - Manifest matches current config -> no-op, proceed.
    - Manifest mismatches and ``force`` is False -> raise, so a bbox/cell-size
      change never silently scores the wrong region. Nothing is deleted.
    - Manifest mismatches and ``force`` is True -> delete generated outputs
      in data/processed/ (not the raw data) and rebaseline the manifest, so
      every ingest step's skip-if-exists check naturally regenerates them.
      If only a layer drifted (e.g. the wind hub height), only that layer's
      outputs are deleted.
    """
    existing = _read_manifest(config)
    current = _current_fields(config)
    diffs = _diff(existing, current) if existing is not None else []
    stale = _stale_layers(existing, config)

    if not diffs and not stale:
        if existing is None:
            logger.info(
                "No processed-data manifest found; baselining data/processed/ "
                "against the current config (bbox=%s, cell_size_m=%s).",
                current["study_area_bbox"], current["cell_size_m"],
            )
        if existing != _current_manifest(config):
            # Baseline, or record layer inputs for layers with no output yet.
            _write_manifest(config)
        return

    all_diffs = diffs + list(stale.values())
    if not force:
        raise StaleProcessedDataError(
            "data/processed/ was built from a different config and may contain "
            "stale outputs:\n  " + "\n  ".join(all_diffs) +
            "\nRun with --force to delete and regenerate the affected outputs, "
            "or point `paths.processed` at a fresh directory for this config."
        )

    processed = config.paths.processed
    removed = 0
    if diffs:
        logger.warning(
            "Config changed since data/processed/ was built — %s. "
            "--force: deleting generated outputs and regenerating.",
            "; ".join(all_diffs),
        )
        for pattern in _GENERATED_GLOBS:
            for path in processed.glob(pattern):
                path.unlink()
                removed += 1
    else:
        logger.warning(
            "Layer inputs changed since data/processed/ was built — %s. "
            "--force: deleting only the affected layer outputs (%s) and regenerating.",
            "; ".join(all_diffs), ", ".join(sorted(stale)),
        )
        for layer in stale:
            for name in _LAYER_OUTPUTS[layer]:
                path = processed / name
                if path.exists():
                    path.unlink()
                    removed += 1
    logger.info("Removed %d stale generated file(s) from %s", removed, processed)
    _write_manifest(config)


def verify_manifest_fresh(config: Config) -> None:
    """Read-only check for entry points that consume (but don't regenerate)
    processed data, e.g. `score`/`visualize`. Raises on a detected mismatch;
    a missing manifest is not an error (legacy/pre-existing data), except
    for a layer output with no record (see the module docstring)."""
    existing = _read_manifest(config)
    diffs = _diff(existing, _current_fields(config)) if existing is not None else []
    diffs += list(_stale_layers(existing, config).values())
    if diffs:
        raise StaleProcessedDataError(
            "data/processed/ was built from a different config than is currently "
            "active:\n  " + "\n  ".join(diffs) +
            "\nRe-run `prepare`/`ingest` (with --force if the config change is "
            "intentional) before scoring."
        )

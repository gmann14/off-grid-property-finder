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
the DEM derivatives). A layer's record is written by that layer's ingest step
only after its output is safely in place (`record_layer`), never at check
time, so a record always describes the file actually on disk. Under
``--force`` a stale layer output is not deleted up front: the ingest step
rebuilds it into a temp file and swaps it in, so a failed re-fetch leaves the
previous file (and its record) intact. Unlike the global fields, an existing
layer output with NO record is treated as stale, not baselined: it can only
have been written by code that predates the record (for wind.tif, the old
100 m-only ingest), so baselining it would bless exactly the stale file this
guards against.
"""

import json
import logging
import os
from pathlib import Path

from src.config import Config
from src.constants import WIND_INGEST_METHOD

logger = logging.getLogger("property_finder")

MANIFEST_FILENAME = ".manifest.json"

# Config fields that define what a processed/ directory's outputs represent.
# If any of these change, the cached outputs no longer match the config.
# ``study_area_name`` is recorded but not tracked: it is only a label (the map
# tooltip), and treating a rename as stale made `--force` delete every
# processed file, REST-fetched parcels included, for no change in the data.
_TRACKED_FIELDS = ("study_area_bbox", "cell_size_m", "working_crs")

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


def _manifest_path(config: Config) -> Path:
    return config.paths.processed / MANIFEST_FILENAME


def _read_manifest(config: Config) -> dict | None:
    path = _manifest_path(config)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        logger.warning("Manifest at %s is unreadable; treating as missing", path)
        return None
    if not isinstance(data, dict):
        logger.warning("Manifest at %s is not a JSON object; treating as missing", path)
        return None
    return data


def _write_json_atomic(path: Path, payload: dict) -> None:
    """Temp file + rename, so a crash can't leave a half-written manifest that
    would later be read as missing and re-baselined."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(payload, indent=2))
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _write_manifest(config: Config, layers: dict | None = None) -> None:
    """Write the current global fields plus ``layers`` (layer -> recorded inputs)."""
    _write_json_atomic(_manifest_path(config), {**_current_fields(config), "layers": dict(layers or {})})


def _recorded_layers(existing: dict | None) -> dict:
    layers = (existing or {}).get("layers")
    return layers if isinstance(layers, dict) else {}


def layer_is_current(config: Config, layer: str) -> bool:
    """True when the manifest's record for ``layer`` matches the current config
    (i.e. its output on disk, if any, was built from these inputs)."""
    return _recorded_layers(_read_manifest(config)).get(layer) == _current_layers(config)[layer]


def record_layer(config: Config, layer: str) -> None:
    """Record that ``layer``'s output was just (re)built from the current config.

    Called by the layer's ingest step after its output is in place. Only the
    ``layers`` entry changes: the recorded global fields are kept as they are,
    so recording one layer can never bless other outputs built for a different
    study area. Without a manifest yet, the global fields are baselined.
    """
    existing = _read_manifest(config)
    layers = _recorded_layers(existing)
    layers[layer] = _current_layers(config)[layer]
    if existing is None:
        _write_manifest(config, layers)
    else:
        _write_json_atomic(_manifest_path(config), {**existing, "layers": layers})


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
    recorded = _recorded_layers(existing)
    stale = {}
    for layer, current in _current_layers(config).items():
        if not any((processed / f).exists() for f in _LAYER_OUTPUTS[layer]):
            continue
        rec = recorded.get(layer)
        if rec is None:
            stale[layer] = (
                f"layers.{layer}: no record (built before its inputs were tracked) -> {current!r}"
            )
        elif not isinstance(rec, dict):
            stale[layer] = f"layers.{layer}: unreadable record {rec!r} -> {current!r}"
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
    - Global fields mismatch and ``force`` is True -> delete generated outputs
      in data/processed/ (not the raw data) and rebaseline the manifest, so
      every ingest step's skip-if-exists check naturally regenerates them.
    - Only a layer drifted (e.g. the wind hub height) and ``force`` is True ->
      nothing is deleted; that layer's ingest step sees its record is stale and
      rebuilds the output in place (atomically), then records it.
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

    if diffs:
        logger.warning(
            "Config changed since data/processed/ was built — %s. "
            "--force: deleting generated outputs and regenerating.",
            "; ".join(all_diffs),
        )
        processed = config.paths.processed
        removed = 0
        for pattern in _GENERATED_GLOBS:
            for path in processed.glob(pattern):
                path.unlink()
                removed += 1
        logger.info("Removed %d stale generated file(s) from %s", removed, processed)
        _write_manifest(config)  # layer records go with their deleted outputs
        return

    logger.warning(
        "Layer inputs changed since data/processed/ was built — %s. "
        "--force: rebuilding only %s (the existing file is replaced only once "
        "the new one is complete).",
        "; ".join(all_diffs), ", ".join(f"{layer} ({', '.join(_LAYER_OUTPUTS[layer])})"
                                        for layer in sorted(stale)),
    )
    if existing is None:
        # Baseline the global fields; the stale layer has no record, so its
        # ingest step will rebuild it.
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

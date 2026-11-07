"""Data smoke-test: check that required files are present and readable."""

import logging
from pathlib import Path

import geopandas as gpd
import rasterio

from src.config import Config
from src.raw_layers import RAW_LAYERS, RawLayer, find_raw_files, search_locations

# Layers that `prepare`/`ingest` fetch directly from a remote service into
# data/processed/ — they need no raw file at all, so they're checked separately
# from RAW_LAYERS (src/raw_layers.py: the manually-downloaded raw inputs).
AUTO_FETCHED_LAYERS = {
    "flood": {
        "processed_name": "flood.gpkg",
        "type": "vector",
        "description": "Coastal flooding (NS Coastal Program) — auto-fetched by `ingest_flood`",
    },
    "wind": {
        "processed_name": "wind.tif",
        "type": "raster",
        "description": "Wind resource (Global Wind Atlas) — auto-fetched by `ingest_wind`",
    },
}


def _check_raster(path: Path, logger: logging.Logger) -> dict:
    try:
        with rasterio.open(path) as src:
            return {
                "path": str(path),
                "status": "ok",
                "crs": str(src.crs),
                "bounds": src.bounds,
                "resolution": src.res,
                "shape": (src.height, src.width),
            }
    except Exception as e:
        logger.warning("Failed to read raster %s: %s", path, e)
        return {"path": str(path), "status": "error", "error": str(e)}


def _check_vector(path: Path, logger: logging.Logger, layer: str | None = None) -> dict:
    try:
        kwargs = {"layer": layer} if layer else {}
        gdf = gpd.read_file(path, rows=1, **kwargs)
        full_count = len(gpd.read_file(path, rows=0, ignore_geometry=True, **kwargs))
        return {
            "path": str(path),
            "status": "ok",
            "crs": str(gdf.crs),
            "geometry_type": str(gdf.geometry.geom_type.iloc[0]) if len(gdf) > 0 else "unknown",
            "columns": list(gdf.columns),
            "feature_count": full_count,
        }
    except Exception as e:
        logger.warning("Failed to read vector %s: %s", path, e)
        return {"path": str(path), "status": "error", "error": str(e)}


def _gdb_layer(path: Path, spec: RawLayer, logger: logging.Logger) -> str | None:
    """The layer to check inside a File Geodatabase: the one ingest reads, if
    the table names one and the geodatabase has it; else the first layer."""
    if path.suffix.lower() != ".gdb" or not spec.gdb_layer:
        return None
    try:
        import pyogrio
        names = [str(n) for n in pyogrio.list_layers(path)[:, 0]]
    except Exception as e:
        # The read in _check_vector reports the unreadable geodatabase.
        logger.debug("Could not list layers of %s: %s", path, e)
        return None
    return spec.gdb_layer if spec.gdb_layer in names else None


def _check_files(files: list[Path], spec: RawLayer, logger: logging.Logger) -> dict:
    """Check every file a layer is read from; the layer is ok only if all are.
    The result describes the first file (or the first failing one) and lists
    them all under ``files``."""
    checks = [
        _check_raster(f, logger) if spec.type == "raster"
        else _check_vector(f, logger, _gdb_layer(f, spec, logger))
        for f in files
    ]
    result = next((c for c in checks if c["status"] != "ok"), checks[0])
    result["files"] = [str(f) for f in files]
    return result


def _check_auto_fetched(config: Config, logger: logging.Logger) -> dict[str, dict]:
    """Report status of layers `prepare` fetches remotely (no raw file needed)."""
    results = {}
    for layer_name, spec in AUTO_FETCHED_LAYERS.items():
        path = config.paths.processed / spec["processed_name"]
        if not path.exists():
            logger.info(
                "%-18s not yet fetched — %s (run `prepare`/`ingest` to fetch)",
                layer_name, spec["description"],
            )
            results[layer_name] = {
                "status": "NOT YET FETCHED (auto)",
                "description": spec["description"],
            }
            continue
        result = _check_raster(path, logger) if spec["type"] == "raster" else _check_vector(path, logger)
        result["description"] = spec["description"]
        results[layer_name] = result
        if result["status"] == "ok":
            logger.info("%-18s OK  %s", layer_name, path.name)
        else:
            logger.error("%-18s ERROR  %s", layer_name, result.get("error", ""))
    return results


def run_check_data(config: Config, logger: logging.Logger) -> dict[str, dict]:
    """Report, for each raw layer, whether the files ingest would read exist
    and are readable (see src/raw_layers.py), plus the auto-fetched layers."""
    results = {}
    raw_dir = config.paths.raw

    for layer_name, spec in RAW_LAYERS.items():
        files = find_raw_files(raw_dir, layer_name)

        if not files:
            status = "MISSING (required)" if spec.required else "MISSING (optional)"
            logger.warning("%s: %s — %s (looked in %s)", layer_name, status, spec.description,
                           search_locations(raw_dir, layer_name))
            results[layer_name] = {"status": status, "description": spec.description}
            continue

        result = _check_files(files, spec, logger)
        result["description"] = spec.description
        results[layer_name] = result

        more = f" (+{len(files) - 1} more)" if len(files) > 1 else ""
        if result["status"] == "ok":
            logger.info("%-18s OK  %s%s", layer_name, files[0].name, more)
            if "crs" in result:
                logger.info("  CRS: %s", result["crs"])
        else:
            logger.error("%-18s ERROR  %s: %s", layer_name, Path(result["path"]).name,
                         result.get("error", ""))

    results.update(_check_auto_fetched(config, logger))

    ok_count = sum(1 for r in results.values() if r.get("status") == "ok")
    total = len(results)
    logger.info("Data check complete: %d/%d layers found and readable", ok_count, total)

    return results

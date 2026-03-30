"""Data smoke-test: check that required files are present and readable."""

import logging
from pathlib import Path

import geopandas as gpd
import rasterio

from src.config import Config

EXPECTED_LAYERS = {
    "dem": {
        "subdir": "dem",
        "type": "raster",
        "extensions": [".tif", ".tiff"],
        "required": True,
        "description": "NS Enhanced DEM (20m)",
    },
    "streams": {
        "subdir": "hydro",
        "type": "vector",
        "extensions": [".shp", ".gpkg", ".geojson"],
        "required": True,
        "description": "NSHN stream network",
    },
    "land_cover": {
        "subdir": "land-cover",
        "type": "vector",
        "extensions": [".shp", ".gpkg", ".geojson"],
        "required": True,
        "description": "NSTDB land cover",
    },
    "roads": {
        "subdir": "roads",
        "type": "vector",
        "extensions": [".shp", ".gpkg", ".geojson", ".pbf"],
        "required": True,
        "description": "Road network (OSM or NSTDB)",
    },
    "protected_areas": {
        "subdir": "exclusions",
        "type": "vector",
        "extensions": [".shp", ".gpkg", ".geojson"],
        "required": True,
        "description": "Protected areas",
    },
    "buildings": {
        "subdir": "buildings",
        "type": "vector",
        "extensions": [".shp", ".gpkg", ".geojson"],
        "required": False,
        "description": "Building footprints",
    },
    "flood": {
        "subdir": "exclusions",
        "type": "vector",
        "extensions": [".shp", ".gpkg", ".geojson"],
        "required": False,
        "description": "Flood/coastal risk",
    },
    "parcels": {
        "subdir": "parcels",
        "type": "vector",
        "extensions": [".shp", ".gpkg", ".gdb"],
        "required": False,
        "description": "Property parcels (Stage B)",
    },
    "civic": {
        "subdir": "civic",
        "type": "vector",
        "extensions": [".shp", ".gpkg", ".geojson"],
        "required": False,
        "description": "Civic address points",
    },
}


def _find_files(directory: Path, extensions: list[str]) -> list[Path]:
    found = []
    if directory.exists():
        for ext in extensions:
            found.extend(directory.glob(f"*{ext}"))
    return found


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


def _check_vector(path: Path, logger: logging.Logger) -> dict:
    try:
        gdf = gpd.read_file(path, rows=1)
        full_count = len(gpd.read_file(path, rows=0, ignore_geometry=True))
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


def run_check_data(config: Config, logger: logging.Logger) -> dict[str, dict]:
    results = {}
    raw_dir = config.paths.raw

    for layer_name, spec in EXPECTED_LAYERS.items():
        layer_dir = raw_dir / spec["subdir"]
        files = _find_files(layer_dir, spec["extensions"])

        if not files:
            status = "MISSING (required)" if spec["required"] else "MISSING (optional)"
            logger.warning("%s: %s — %s", layer_name, status, spec["description"])
            results[layer_name] = {"status": status, "description": spec["description"]}
            continue

        file_path = files[0]
        if spec["type"] == "raster":
            result = _check_raster(file_path, logger)
        else:
            result = _check_vector(file_path, logger)

        result["description"] = spec["description"]
        results[layer_name] = result

        if result["status"] == "ok":
            logger.info("%-18s OK  %s", layer_name, file_path.name)
            if "crs" in result:
                logger.info("  CRS: %s", result["crs"])
        else:
            logger.error("%-18s ERROR  %s", layer_name, result.get("error", ""))

    ok_count = sum(1 for r in results.values() if r.get("status") == "ok")
    total = len(results)
    logger.info("Data check complete: %d/%d layers found and readable", ok_count, total)

    return results

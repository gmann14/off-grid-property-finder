"""DEM derivative generation: slope, aspect, flow direction, flow accumulation."""

import logging
from pathlib import Path

import numpy as np
import rasterio

logger = logging.getLogger("property_finder")


def generate_slope(dem_path: Path, out_path: Path) -> Path:
    if out_path.exists():
        logger.debug("Slope raster exists, skipping: %s", out_path)
        return out_path

    with rasterio.open(dem_path) as src:
        dem = src.read(1).astype(np.float64)
        res_x, res_y = src.res
        meta = src.meta.copy()

    # Gradient-based slope in degrees
    dy, dx = np.gradient(dem, res_y, res_x)
    slope_rad = np.arctan(np.sqrt(dx**2 + dy**2))
    slope_deg = np.degrees(slope_rad)

    meta.update(dtype="float32", nodata=-9999)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out_path, "w", **meta) as dst:
        dst.write(slope_deg.astype(np.float32), 1)

    logger.info("Generated slope raster: %s", out_path)
    return out_path


def generate_aspect(dem_path: Path, out_path: Path) -> Path:
    if out_path.exists():
        logger.debug("Aspect raster exists, skipping: %s", out_path)
        return out_path

    with rasterio.open(dem_path) as src:
        dem = src.read(1).astype(np.float64)
        res_x, res_y = src.res
        meta = src.meta.copy()

    dy, dx = np.gradient(dem, res_y, res_x)
    # Aspect: clockwise from north (0-360)
    aspect = np.degrees(np.arctan2(-dx, dy))
    aspect = np.where(aspect < 0, aspect + 360, aspect)
    # Flat areas (no slope) get aspect = -1
    flat_mask = (np.abs(dx) < 1e-10) & (np.abs(dy) < 1e-10)
    aspect = np.where(flat_mask, -1, aspect)

    meta.update(dtype="float32", nodata=-9999)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out_path, "w", **meta) as dst:
        dst.write(aspect.astype(np.float32), 1)

    logger.info("Generated aspect raster: %s", out_path)
    return out_path


def generate_flow_direction(dem_path: Path, out_path: Path) -> Path:
    """Generate flow direction using WhiteboxTools D8 algorithm."""
    if out_path.exists():
        logger.debug("Flow direction raster exists, skipping: %s", out_path)
        return out_path

    try:
        import whitebox

        wbt = whitebox.WhiteboxTools()
        wbt.verbose = False

        # WhiteboxTools requires absolute paths
        dem_abs = str(dem_path.resolve())
        out_abs = str(out_path.resolve())

        # WhiteboxTools requires filling depressions first
        filled_path = out_path.parent / "dem_filled.tif"
        filled_abs = str(filled_path.resolve())
        if not filled_path.exists():
            wbt.fill_depressions(dem_abs, filled_abs)
            logger.info("Filled DEM depressions: %s", filled_path)

        wbt.d8_pointer(filled_abs, out_abs)
        logger.info("Generated flow direction raster: %s", out_path)

    except ImportError:
        logger.warning(
            "WhiteboxTools not available. Skipping flow direction. "
            "Install with: pip install whitebox"
        )
        return out_path

    return out_path


def generate_flow_accumulation(dem_path: Path, out_path: Path) -> Path:
    """Generate flow accumulation using WhiteboxTools D8 algorithm."""
    if out_path.exists():
        logger.debug("Flow accumulation raster exists, skipping: %s", out_path)
        return out_path

    try:
        import whitebox

        wbt = whitebox.WhiteboxTools()
        wbt.verbose = False

        # WhiteboxTools requires absolute paths
        dem_abs = str(dem_path.resolve())
        out_abs = str(out_path.resolve())

        # Needs filled DEM and flow direction
        filled_path = out_path.parent / "dem_filled.tif"
        filled_abs = str(filled_path.resolve())
        flow_dir_path = out_path.parent / "flow_direction.tif"
        flow_dir_abs = str(flow_dir_path.resolve())

        if not filled_path.exists():
            wbt.fill_depressions(dem_abs, filled_abs)

        if not flow_dir_path.exists():
            wbt.d8_pointer(filled_abs, flow_dir_abs)

        wbt.d8_flow_accumulation(flow_dir_abs, out_abs, out_type="cells")
        logger.info("Generated flow accumulation raster: %s", out_path)

    except ImportError:
        logger.warning(
            "WhiteboxTools not available. Skipping flow accumulation. "
            "Install with: pip install whitebox"
        )
        return out_path

    return out_path

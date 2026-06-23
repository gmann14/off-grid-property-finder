"""DEM derivative generation: slope, aspect, flow direction, flow accumulation."""

import logging
from pathlib import Path

import numpy as np
import rasterio

logger = logging.getLogger("property_finder")


def _compress_raster(path: Path, target_dtype: str | None = None) -> None:
    """Re-write a raster in-place with LZW compression (and optional dtype cast)."""
    with rasterio.open(path) as src:
        if src.compression == rasterio.enums.Compression.lzw and target_dtype is None:
            return
        data = src.read()
        meta = src.meta.copy()
    dtype = target_dtype or meta["dtype"]
    meta.update(compress="lzw", dtype=dtype)
    with rasterio.open(path, "w", **meta) as dst:
        dst.write(data.astype(dtype))


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

    meta.update(dtype="float32", nodata=-9999, compress="lzw")
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

    meta.update(dtype="float32", nodata=-9999, compress="lzw")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out_path, "w", **meta) as dst:
        dst.write(aspect.astype(np.float32), 1)

    logger.info("Generated aspect raster: %s", out_path)
    return out_path


def generate_exposure(dem_path: Path, out_path: Path, radius_m: float = 1000.0) -> Path:
    """Generate a Topographic Position Index (TPI) raster for wind exposure.

    TPI = cell elevation minus the mean elevation of a surrounding window.
    Positive = ridge/hilltop (wind-exposed); negative = valley (sheltered).
    Used as a wind proxy when no wind-resource raster is available.
    """
    if out_path.exists():
        logger.debug("Exposure raster exists, skipping: %s", out_path)
        return out_path

    try:
        from scipy.ndimage import uniform_filter
    except ImportError:
        logger.warning("scipy not installed; skipping exposure (TPI) raster")
        return out_path

    with rasterio.open(dem_path) as src:
        dem = src.read(1).astype(np.float32)
        res_x, _ = src.res
        meta = src.meta.copy()
        nodata = src.nodata

    valid = np.ones_like(dem, dtype=bool)
    if nodata is not None:
        valid = dem != nodata
    filled = np.where(valid, dem, np.nan)

    window = max(3, int(round((2 * radius_m) / abs(res_x))))
    # Mean of surrounding elevations, ignoring NaN, via ratio of filtered sums.
    data0 = np.where(valid, filled, 0.0)
    cnt = uniform_filter(valid.astype(np.float32), size=window, mode="nearest")
    s = uniform_filter(data0, size=window, mode="nearest")
    with np.errstate(invalid="ignore", divide="ignore"):
        local_mean = np.where(cnt > 0, s / cnt, 0.0)
    tpi = np.where(valid, filled - local_mean, -9999.0).astype(np.float32)

    meta.update(dtype="float32", nodata=-9999, compress="lzw")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out_path, "w", **meta) as dst:
        dst.write(tpi, 1)

    logger.info("Generated exposure (TPI) raster: %s (window=%d px)", out_path, window)
    return out_path


def _condition_dem(wbt, dem_abs: str, out_abs: str) -> None:
    """Hydrologically condition a DEM for flow routing.

    Prefers least-cost breaching (handles the large flats in coastal NS where
    plain depression-filling leaves flow undefined and accumulation fails to
    propagate); falls back to fill_depressions if breaching is unavailable.
    """
    if hasattr(wbt, "breach_depressions_least_cost"):
        wbt.breach_depressions_least_cost(dem_abs, out_abs, dist=200, fill=True)
    elif hasattr(wbt, "breach_depressions"):
        wbt.breach_depressions(dem_abs, out_abs)
    else:
        wbt.fill_depressions(dem_abs, out_abs)


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

        # WhiteboxTools requires a hydrologically conditioned DEM first
        filled_path = out_path.parent / "dem_filled.tif"
        filled_abs = str(filled_path.resolve())
        if not filled_path.exists():
            _condition_dem(wbt, dem_abs, filled_abs)
            logger.info("Conditioned DEM (breach/fill): %s", filled_path)

        wbt.d8_pointer(filled_abs, out_abs)
        _compress_raster(out_path)
        _compress_raster(filled_path, target_dtype="float32")
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
            _condition_dem(wbt, dem_abs, filled_abs)

        if not flow_dir_path.exists():
            wbt.d8_pointer(filled_abs, flow_dir_abs)

        wbt.d8_flow_accumulation(flow_dir_abs, out_abs, out_type="cells")
        _compress_raster(out_path)

        # Sanity-check propagation: a valid accumulation over a real study area
        # reaches river scale. Warn loudly if it didn't (e.g., flats broke D8).
        try:
            with rasterio.open(out_path) as src:
                arr = src.read(1)
                amax = float(np.nanmax(arr[arr != (src.nodata if src.nodata is not None else np.nan)]))
            if amax < 10000:
                logger.warning(
                    "Flow accumulation max is only %.0f cells — likely failed to "
                    "propagate (flats). Hydro will fall back to the segment proxy.",
                    amax,
                )
            logger.info("Generated flow accumulation raster: %s (max=%.0f cells)", out_path, amax)
        except Exception:
            logger.info("Generated flow accumulation raster: %s", out_path)

    except ImportError:
        logger.warning(
            "WhiteboxTools not available. Skipping flow accumulation. "
            "Install with: pip install whitebox"
        )
        return out_path

    return out_path

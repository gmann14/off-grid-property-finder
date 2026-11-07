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


DERIVATIVE_NODATA = -9999.0


def _read_dem_nan(dem_path: Path):
    """Read band 1 as float64 with nodata (and non-finite values) set to NaN.

    Returns (dem, res_x, res_y, meta).
    """
    with rasterio.open(dem_path) as src:
        dem = src.read(1).astype(np.float64)
        res_x, res_y = src.res
        meta = src.meta.copy()
        nodata = src.nodata
    if nodata is not None and not np.isnan(nodata):
        dem[dem == nodata] = np.nan
    dem[~np.isfinite(dem)] = np.nan
    return dem, res_x, res_y, meta


def _dem_gradients(dem: np.ndarray, res_x: float, res_y: float):
    """np.gradient of a NaN-holed DEM -> (dx, dy, valid).

    A central difference at a pixel uses its two neighbours, not the pixel
    itself, so a pixel is valid only if it has data AND both gradients are
    finite: nodata pixels and the ring next to them come out invalid instead
    of slope 0 inside the hole and a cliff (~90 deg) across the sentinel at
    its edge.
    """
    dy, dx = np.gradient(dem, res_y, res_x)
    valid = np.isfinite(dem) & np.isfinite(dx) & np.isfinite(dy)
    return dx, dy, valid


def generate_slope(dem_path: Path, out_path: Path) -> Path:
    if out_path.exists():
        logger.debug("Slope raster exists, skipping: %s", out_path)
        return out_path

    dem, res_x, res_y, meta = _read_dem_nan(dem_path)

    # Gradient-based slope in degrees
    dx, dy, valid = _dem_gradients(dem, res_x, res_y)
    slope_deg = np.degrees(np.arctan(np.sqrt(dx**2 + dy**2)))
    slope_deg = np.where(valid, slope_deg, DERIVATIVE_NODATA)

    meta.update(dtype="float32", nodata=DERIVATIVE_NODATA, compress="lzw")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out_path, "w", **meta) as dst:
        dst.write(slope_deg.astype(np.float32), 1)

    logger.info("Generated slope raster: %s", out_path)
    return out_path


def generate_aspect(dem_path: Path, out_path: Path) -> Path:
    if out_path.exists():
        logger.debug("Aspect raster exists, skipping: %s", out_path)
        return out_path

    dem, res_x, res_y, meta = _read_dem_nan(dem_path)

    dx, dy, valid = _dem_gradients(dem, res_x, res_y)
    # Aspect: clockwise from north (0-360)
    aspect = np.degrees(np.arctan2(-dx, dy))
    aspect = np.where(aspect < 0, aspect + 360, aspect)
    # Flat areas (no slope) get aspect = -1
    flat_mask = (np.abs(dx) < 1e-10) & (np.abs(dy) < 1e-10)
    aspect = np.where(flat_mask, -1, aspect)
    aspect = np.where(valid, aspect, DERIVATIVE_NODATA)

    meta.update(dtype="float32", nodata=DERIVATIVE_NODATA, compress="lzw")
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


def _resample_dem(src_path: Path, dst_path: Path, target_res_m: float,
                  sea_level_m: float | None = None) -> Path:
    """Average-resample a DEM to a coarser resolution for flow routing.

    D8 flow accumulation propagates through coastal flats far better at ~20m
    than at 5m (where flat-cell flow direction is undefined and accumulation
    fragments). Used only for routing; scoring still reads the native DEM.

    Nodata cells are excluded from the block average (block-mean of valid cells
    only); an all-nodata block stays nodata. Averaging across the nodata
    sentinel would otherwise inject garbage elevations into routing. With
    ``sea_level_m`` set, pixels at or below it are treated as nodata too, so
    the sea is not routed and a coastal block averages its land pixels only.
    """
    import warnings

    with rasterio.open(src_path) as src:
        factor = max(1, int(round(target_res_m / abs(src.res[0]))))
        dem = src.read(1).astype("float32")
        nodata = src.nodata
        base_transform = src.transform
        meta = src.meta.copy()

    out_nodata = nodata if nodata is not None else -32767.0
    invalid = ~np.isfinite(dem)
    if nodata is not None:
        invalid |= dem == nodata
    if sea_level_m is not None:
        invalid |= dem <= sea_level_m
    if factor == 1:
        out = np.where(invalid, out_nodata, dem).astype("float32")
    else:
        dem = np.where(invalid, np.nan, dem)
        nh, nw = dem.shape[0] // factor, dem.shape[1] // factor
        dem = dem[: nh * factor, : nw * factor].reshape(nh, factor, nw, factor)
        with warnings.catch_warnings():  # all-nan block → nan (expected)
            warnings.simplefilter("ignore", RuntimeWarning)
            out = np.nanmean(dem, axis=(1, 3))
        out = np.where(np.isnan(out), out_nodata, out).astype("float32")

    new_transform = base_transform * base_transform.scale(factor, factor)
    meta.update(height=out.shape[0], width=out.shape[1], transform=new_transform,
                dtype="float32", nodata=out_nodata, compress="lzw")
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(dst_path, "w", **meta) as dst:
        dst.write(out, 1)
    return dst_path


def _condition_dem(wbt, dem_abs: str, out_abs: str) -> None:
    """Hydrologically condition a DEM for flow routing.

    Uses fast O(n) breaching (``breach_depressions`` with ``fill_pits``), which
    breaches what it can and fills the rest — guaranteeing a depressionless DEM
    so D8 propagates. NOTE: ``breach_depressions_least_cost`` with a large
    ``dist`` does a per-pit least-cost search and is pathologically slow on
    large grids (it ran 25+ min without finishing here), so it is avoided.
    """
    if hasattr(wbt, "breach_depressions"):
        wbt.breach_depressions(dem_abs, out_abs, fill_pits=True)
    elif hasattr(wbt, "breach_depressions_least_cost"):
        wbt.breach_depressions_least_cost(dem_abs, out_abs, dist=10, fill=True)
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


def generate_flow_accumulation(
    dem_path: Path, out_path: Path, target_res_m: float | None = None
) -> Path:
    """Generate flow accumulation using WhiteboxTools D8 algorithm.

    When ``target_res_m`` is set and finer than the DEM, routing is done on an
    average-resampled copy at that resolution — essential on fine (5m) coastal
    DEMs where D8 fragments on flats. The output accumulation raster is at the
    routing resolution; the hydro scorer reads drainage area by coordinate, so
    a coarser routing grid is fine.

    Routing runs on dem_flowres.tif, a copy of the DEM (resampled when
    ``target_res_m`` is coarser) with the sea, pixels at or below SEA_LEVEL_M
    as in the buildability mask, set to nodata. WhiteboxTools then treats the
    whole coastline as an outlet, and the sea neither accumulates the land's
    flow nor gets its own pits breached through the shore. The conditioned
    DEM and pointer are rebuilt from it every time the accumulation is, so a
    rerun can't reuse ones conditioned from an older routing DEM.
    """
    from src.constants import FLOW_ACCUM_MIN_VALID_CELLS, SEA_LEVEL_M

    if out_path.exists():
        logger.debug("Flow accumulation raster exists, skipping: %s", out_path)
        return out_path

    try:
        import whitebox

        wbt = whitebox.WhiteboxTools()
        wbt.verbose = False

        # Route on a land-only copy, coarser if asked (handles coastal flats)
        with rasterio.open(dem_path) as s:
            cur_res = abs(s.res[0])
        routing_res = cur_res
        if target_res_m is not None and cur_res < target_res_m:
            routing_res = target_res_m
            logger.info("Flow routing on %.0fm-resampled DEM (native %.1fm)",
                        target_res_m, cur_res)
        routing_dem = out_path.parent / "dem_flowres.tif"
        _resample_dem(dem_path, routing_dem, routing_res, sea_level_m=SEA_LEVEL_M)
        logger.info("Flow routing: DEM pixels at or below %.1f m (sea) are nodata",
                    SEA_LEVEL_M)

        # WhiteboxTools requires absolute paths
        dem_abs = str(Path(routing_dem).resolve())
        out_abs = str(out_path.resolve())

        # Conditioned DEM + pointer, keyed to the ROUTING dem's name so a coarse
        # (e.g. 20m) run can't reuse a native-resolution dem_filled.tif left by
        # generate_flow_direction (resolution/shape mismatch).
        stem = Path(routing_dem).stem
        filled_path = out_path.parent / f"{stem}_filled.tif"
        filled_abs = str(filled_path.resolve())
        flow_dir_path = out_path.parent / f"{stem}_dir.tif"
        flow_dir_abs = str(flow_dir_path.resolve())

        _condition_dem(wbt, dem_abs, filled_abs)
        wbt.d8_pointer(filled_abs, flow_dir_abs)

        # IMPORTANT: pass the conditioned DEM, not the pointer. d8_flow_accumulation
        # treats its input as a DEM unless pntr=True — feeding it the pointer made
        # accumulation degenerate (max ~45 cells regardless of DEM/resolution).
        wbt.d8_flow_accumulation(filled_abs, out_abs, out_type="cells")
        _compress_raster(out_path)

        # Sanity-check propagation: a valid accumulation over a real study area
        # reaches river scale. Warn loudly if it didn't (e.g., flats broke D8).
        # The sea is nodata, so this is the land's maximum.
        try:
            with rasterio.open(out_path) as src:
                arr = src.read(1)
                valid = arr[arr != src.nodata] if src.nodata is not None else arr
                amax = float(np.nanmax(valid)) if valid.size else 0.0
        except Exception as exc:
            logger.warning("Generated flow accumulation raster %s but could not read it "
                           "back to check it (%s: %s)", out_path, type(exc).__name__, exc)
            return out_path
        if amax < FLOW_ACCUM_MIN_VALID_CELLS:
            logger.warning(
                "Flow accumulation max on land is only %.0f cells — likely failed to "
                "propagate (flats). Hydro will fall back to the segment proxy.",
                amax,
            )
        logger.info("Generated flow accumulation raster: %s (land max=%.0f cells)",
                    out_path, amax)

    except ImportError:
        logger.warning(
            "WhiteboxTools not available. Skipping flow accumulation. "
            "Install with: pip install whitebox"
        )
        return out_path

    return out_path

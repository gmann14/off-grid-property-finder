"""Solar suitability scoring based on aspect, slope, and open-area analysis."""

import logging
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.features import geometry_mask
from rasterio.windows import from_bounds

from src.config import Config
from src.constants import (
    SOLAR_ACCEPTABLE_ASPECT,
    SOLAR_ACCEPTABLE_SLOPE,
    SOLAR_FLAT_SLOPE,
    SOLAR_OPTIMAL_ASPECT,
    SOLAR_OPTIMAL_SLOPE,
    SOLAR_PERCENT_THRESHOLDS,
)
from src.scoring.registry import register

logger = logging.getLogger("property_finder")


def _classify_solar_pixel(aspect: float, slope: float) -> int:
    """Classify a single pixel for solar suitability.

    Returns: 2 = optimal, 1 = acceptable, 0 = poor.
    """
    if slope < SOLAR_FLAT_SLOPE:
        return 2

    opt_lo, opt_hi = SOLAR_OPTIMAL_ASPECT
    acc_lo, acc_hi = SOLAR_ACCEPTABLE_ASPECT
    opt_slope_lo, opt_slope_hi = SOLAR_OPTIMAL_SLOPE
    acc_slope_lo, acc_slope_hi = SOLAR_ACCEPTABLE_SLOPE

    in_optimal_aspect = opt_lo <= aspect <= opt_hi
    in_acceptable_aspect = acc_lo <= aspect <= acc_hi
    in_optimal_slope = opt_slope_lo <= slope <= opt_slope_hi
    in_acceptable_slope = acc_slope_lo <= slope <= acc_slope_hi

    if in_optimal_aspect and in_optimal_slope:
        return 2
    if in_acceptable_aspect and in_acceptable_slope:
        return 1
    return 0


def _classify_solar_vectorized(aspect: np.ndarray, slope: np.ndarray) -> np.ndarray:
    """Vectorized classification of pixel arrays. Returns array of 0/1/2."""
    result = np.zeros_like(slope, dtype=np.int8)

    # Flat terrain = optimal
    flat = slope < SOLAR_FLAT_SLOPE
    result[flat] = 2

    # Non-flat: check aspect + slope ranges
    not_flat = ~flat
    opt_lo, opt_hi = SOLAR_OPTIMAL_ASPECT
    acc_lo, acc_hi = SOLAR_ACCEPTABLE_ASPECT
    opt_slope_lo, opt_slope_hi = SOLAR_OPTIMAL_SLOPE
    acc_slope_lo, acc_slope_hi = SOLAR_ACCEPTABLE_SLOPE

    opt_asp = not_flat & (aspect >= opt_lo) & (aspect <= opt_hi)
    opt_slp = (slope >= opt_slope_lo) & (slope <= opt_slope_hi)
    result[opt_asp & opt_slp] = 2

    acc_asp = not_flat & (aspect >= acc_lo) & (aspect <= acc_hi)
    acc_slp = (slope >= acc_slope_lo) & (slope <= acc_slope_hi)
    # Only set acceptable where not already optimal
    acc_mask = acc_asp & acc_slp & (result < 2)
    result[acc_mask] = 1

    return result


def _lookup_score(value: float, thresholds: list[tuple]) -> int:
    for low, high, score in thresholds:
        if low <= value < high:
            return score
    return 0


@register("solar")
def score_solar(candidates: gpd.GeoDataFrame, config: Config) -> pd.Series:
    """Score cells by percentage of area with qualifying solar aspect/slope.

    Uses windowed raster reads for efficiency rather than raster_out zonal stats.
    """
    aspect_path = config.paths.processed / "aspect.tif"
    slope_path = config.paths.processed / "slope.tif"

    if not aspect_path.exists() or not slope_path.exists():
        logger.warning("Aspect/slope rasters not found; solar scores = 0")
        return pd.Series(0.0, index=candidates.index)

    asp_src = rasterio.open(aspect_path)
    slp_src = rasterio.open(slope_path)
    asp_nodata = asp_src.nodata
    slp_nodata = slp_src.nodata

    scores = []
    for idx, cell in candidates.iterrows():
        bounds = cell.geometry.bounds  # (minx, miny, maxx, maxy)
        try:
            asp_win = from_bounds(*bounds, asp_src.transform)
            slp_win = from_bounds(*bounds, slp_src.transform)

            asp_data = asp_src.read(1, window=asp_win)
            slp_data = slp_src.read(1, window=slp_win)

            # Mask nodata
            valid = np.ones_like(asp_data, dtype=bool)
            if asp_nodata is not None:
                valid &= asp_data != asp_nodata
            if slp_nodata is not None:
                valid &= slp_data != slp_nodata

            asp_valid = asp_data[valid]
            slp_valid = slp_data[valid]

            if len(asp_valid) == 0:
                scores.append(0)
                continue

            classifications = _classify_solar_vectorized(asp_valid, slp_valid)
            qualifying = (classifications >= 1).sum()
            pct = (qualifying / len(asp_valid)) * 100
            scores.append(_lookup_score(pct, SOLAR_PERCENT_THRESHOLDS))
        except Exception:
            scores.append(0)

    asp_src.close()
    slp_src.close()

    return pd.Series(scores, index=candidates.index, dtype=float)

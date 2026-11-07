"""Open-ground capacity scoring — merges buildability and solar room.

Replaces the old (saturated, aspect-only) solar criterion and the (constant)
buildable criterion with one area-based metric: how much of a cell is flat,
open, unbuilt, non-water ground (from the fixed buildability mask), with a
small bonus when a good share of that ground faces south. In NS, PV yield is
near-uniform, so usable solar potential is mostly about available area, not
aspect — and the same open, flat ground is what you build a house on.
"""

import logging

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio

from src.config import Config
from src.constants import (
    OPEN_GROUND_PERCENT_THRESHOLDS,
    OPEN_GROUND_SOUTH_BONUS,
    OPEN_GROUND_SOUTH_FRACTION,
    SOLAR_ACCEPTABLE_ASPECT,
)
from src.scoring.bands import band_score
from src.scoring.registry import register
from src.zonal import grid_zonal_stats

logger = logging.getLogger("property_finder")


@register("open_ground")
def score_open_ground(candidates: gpd.GeoDataFrame, config: Config) -> pd.Series:
    """Score cells by usable open, buildable ground area (+ small south bonus)."""
    mask_path = config.paths.processed / "buildability_mask.tif"
    aspect_path = config.paths.processed / "aspect.tif"

    if not mask_path.exists():
        logger.warning("Buildability mask not found; open_ground scores = 0")
        return pd.Series(0.0, index=candidates.index)

    # Buildable mask is 0/1 (nodata 255) → mean = fraction open/buildable ground.
    means = grid_zonal_stats(candidates.geometry, mask_path, stats=("mean",), nodata=255)["mean"]
    base = [0 if np.isnan(m) else band_score(m * 100, OPEN_GROUND_PERCENT_THRESHOLDS) for m in means]

    south = _open_ground_south_share(candidates, mask_path, aspect_path)

    # The bonus is for open ground that faces south: none without open ground.
    scores = [
        min(100.0, b + (OPEN_GROUND_SOUTH_BONUS
                        if b > 0 and frac >= OPEN_GROUND_SOUTH_FRACTION else 0.0))
        for b, frac in zip(base, south)
    ]
    return pd.Series(scores, index=candidates.index, dtype=float)


def _open_ground_south_share(candidates: gpd.GeoDataFrame, mask_path, aspect_path) -> np.ndarray:
    """Per cell, the share of its OPEN (buildable) pixels whose aspect is in
    SOLAR_ACCEPTABLE_ASPECT; 0 for a cell with no open pixels, or everywhere
    when there is no aspect raster or it isn't on the mask's pixel grid."""
    n = len(candidates)
    if not aspect_path.exists():
        logger.warning("Aspect raster not found; no open-ground south bonus")
        return np.zeros(n)
    with rasterio.open(mask_path) as m, rasterio.open(aspect_path) as a:
        aligned = m.shape == a.shape and m.transform.almost_equals(a.transform)
        open_px = m.read(1) == 1 if aligned else None
    if not aligned:
        logger.warning("Aspect raster %s is not on the buildability mask's pixel grid; "
                       "no open-ground south bonus", aspect_path)
        return np.zeros(n)
    return grid_zonal_stats(
        candidates.geometry, aspect_path, stats=("frac",),
        frac_range=SOLAR_ACCEPTABLE_ASPECT, nodata=-9999, where=open_px,
    )["frac"]

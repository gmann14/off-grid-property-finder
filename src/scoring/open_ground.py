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

from src.config import Config
from src.constants import (
    OPEN_GROUND_PERCENT_THRESHOLDS,
    OPEN_GROUND_SOUTH_BONUS,
    OPEN_GROUND_SOUTH_FRACTION,
    SOLAR_ACCEPTABLE_ASPECT,
)
from src.scoring.registry import register
from src.zonal import grid_zonal_stats

logger = logging.getLogger("property_finder")


def _lookup_score(value: float, thresholds: list[tuple]) -> int:
    for low, high, score in thresholds:
        if low <= value < high:
            return score
    return 0


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
    base = [0 if np.isnan(m) else _lookup_score(m * 100, OPEN_GROUND_PERCENT_THRESHOLDS) for m in means]

    # South-facing share of the cell (vectorized fraction in acceptable aspect)
    if aspect_path.exists():
        south = grid_zonal_stats(
            candidates.geometry, aspect_path, stats=("frac",),
            frac_range=SOLAR_ACCEPTABLE_ASPECT, nodata=-9999,
        )["frac"]
    else:
        south = np.zeros(len(candidates))

    scores = [
        min(100.0, b + (OPEN_GROUND_SOUTH_BONUS if frac >= OPEN_GROUND_SOUTH_FRACTION else 0.0))
        for b, frac in zip(base, south)
    ]
    return pd.Series(scores, index=candidates.index, dtype=float)

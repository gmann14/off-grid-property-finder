"""Elevation/terrain scoring via zonal statistics on DEM."""

import logging
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

from src.config import Config
from src.constants import ELEVATION_THRESHOLDS
from src.scoring.registry import register
from src.zonal import grid_zonal_stats

logger = logging.getLogger("property_finder")


def _lookup_score(value: float, thresholds: list[tuple]) -> int:
    """Look up a score from a threshold table. First match wins."""
    for low, high, score in thresholds:
        if low <= value < high:
            return score
    return 0


@register("elevation")
def score_elevation(candidates: gpd.GeoDataFrame, config: Config) -> pd.Series:
    """Score cells by mean elevation from DEM using zonal statistics."""
    dem_path = config.paths.processed / "dem.tif"

    if not dem_path.exists():
        logger.warning("DEM not found at %s; elevation scores = 0", dem_path)
        return pd.Series(0.0, index=candidates.index)

    # Fast vectorized zonal mean (nodata read from raster metadata)
    means = grid_zonal_stats(candidates.geometry, dem_path, stats=("mean",))["mean"]

    scores = [
        50 if np.isnan(m) else _lookup_score(m, ELEVATION_THRESHOLDS)  # no DEM → neutral 50
        for m in means
    ]
    no_data_count = int(np.isnan(means).sum())
    if no_data_count > 0:
        logger.info("Elevation: %d cells had no DEM coverage (assigned neutral score 50)",
                     no_data_count)

    return pd.Series(scores, index=candidates.index, dtype=float)

"""Elevation/terrain scoring via zonal statistics on DEM."""

import logging
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

from src.config import Config
from src.constants import ELEVATION_THRESHOLDS, SEA_LEVEL_M
from src.scoring.bands import band_score
from src.scoring.registry import register
from src.zonal import grid_zonal_stats

logger = logging.getLogger("property_finder")


@register("elevation")
def score_elevation(candidates: gpd.GeoDataFrame, config: Config) -> pd.Series:
    """Score cells by mean elevation from DEM using zonal statistics."""
    dem_path = config.paths.processed / "dem.tif"

    if not dem_path.exists():
        logger.warning("DEM not found at %s; elevation scores = 0", dem_path)
        return pd.Series(0.0, index=candidates.index)

    # Mean over the cell's LAND pixels. The DEM stores the sea as <= SEA_LEVEL_M
    # (the rule the buildability mask uses too), so a whole-cell mean drags a
    # shore cell's land into a lower coastal band. A cell with no land pixels
    # keeps its whole-cell mean (<= 0 m, so 0 or 10); no DEM at all stays NaN.
    whole = grid_zonal_stats(candidates.geometry, dem_path, stats=("mean",))["mean"]
    land = grid_zonal_stats(candidates.geometry, dem_path, stats=("mean",),
                            where=lambda dem: dem > SEA_LEVEL_M)["mean"]
    means = np.where(np.isnan(land), whole, land)

    scores = [
        50 if np.isnan(m) else band_score(m, ELEVATION_THRESHOLDS)  # no DEM → neutral 50
        for m in means
    ]
    shore = sum(
        1 for w, s in zip(whole, scores)
        if not np.isnan(w) and band_score(w, ELEVATION_THRESHOLDS) != s
    )
    if shore:
        logger.info("Elevation: %d shore cells scored on their land pixels "
                    "(the whole-cell mean, sea included, gave a different band)", shore)
    no_data_count = int(np.isnan(means).sum())
    if no_data_count > 0:
        logger.info("Elevation: %d cells had no DEM coverage (assigned neutral score 50)",
                     no_data_count)

    return pd.Series(scores, index=candidates.index, dtype=float)

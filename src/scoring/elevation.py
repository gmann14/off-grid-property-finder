"""Elevation/terrain scoring via zonal statistics on DEM."""

import logging
from pathlib import Path

import geopandas as gpd
import pandas as pd

from src.config import Config
from src.constants import ELEVATION_THRESHOLDS
from src.scoring.registry import register

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

    try:
        from rasterstats import zonal_stats
    except ImportError:
        logger.warning("rasterstats not installed; elevation scores = 0")
        return pd.Series(0.0, index=candidates.index)

    # Let rasterstats read nodata from the raster metadata
    # (HRDEM uses -32767, CDEM uses different values)
    stats = zonal_stats(
        candidates.geometry,
        str(dem_path),
        stats=["mean"],
    )

    scores = []
    no_data_count = 0
    for stat in stats:
        mean_elev = stat.get("mean")
        if mean_elev is None:
            # No DEM coverage — assign neutral score rather than penalizing
            scores.append(50)
            no_data_count += 1
        else:
            scores.append(_lookup_score(mean_elev, ELEVATION_THRESHOLDS))

    if no_data_count > 0:
        logger.info("Elevation: %d cells had no DEM coverage (assigned neutral score 50)",
                     no_data_count)

    return pd.Series(scores, index=candidates.index, dtype=float)

"""Wind scoring — Global Wind Atlas resource when available, else exposure proxy.

Wind is a conditional winter-redundancy bonus in NS, not a core driver. When
``processed/wind.tif`` is present (Global Wind Atlas mean speed estimated at a
small off-grid turbine's hub height, default 30 m — see `ingest_wind`) it is
used directly; otherwise terrain exposure (the Topographic Position Index
raster) is a lower-confidence proxy — a turbine wants an exposed ridge/hilltop,
not a sheltered valley.
"""

import logging

import geopandas as gpd
import numpy as np
import pandas as pd

from src.config import Config
from src.constants import EXPOSURE_TPI_THRESHOLDS, WIND_NO_DATA_SCORE, WIND_SPEED_THRESHOLDS
from src.scoring.bands import band_score
from src.scoring.registry import register
from src.zonal import grid_zonal_stats

logger = logging.getLogger("property_finder")


def wind_uses_proxy(config: Config) -> bool:
    """True when wind is scored from terrain exposure rather than a wind raster."""
    return not (config.paths.processed / "wind.tif").exists()


@register("wind")
def score_wind(candidates: gpd.GeoDataFrame, config: Config) -> pd.Series:
    """Score cells by wind resource (GWA raster) or terrain exposure (TPI proxy)."""
    wind_path = config.paths.processed / "wind.tif"
    exposure_path = config.paths.processed / "exposure.tif"

    if wind_path.exists():
        means = grid_zonal_stats(candidates.geometry, wind_path, stats=("mean",))["mean"]
        # A cell the raster doesn't cover has an unknown wind, not a calm one.
        scores = [WIND_NO_DATA_SCORE if np.isnan(m) else band_score(m, WIND_SPEED_THRESHOLDS)
                  for m in means]
        n_missing = int(np.isnan(np.asarray(means, dtype=float)).sum())
        if n_missing:
            logger.warning("Wind: %d cell(s) have no wind-raster value; scored a neutral %d, "
                           "not as calm", n_missing, WIND_NO_DATA_SCORE)
        logger.info("Wind: scored from Global Wind Atlas speed at %.0fm hub height",
                    config.wind.hub_height_m)
        return pd.Series(scores, index=candidates.index, dtype=float)

    if exposure_path.exists():
        means = grid_zonal_stats(candidates.geometry, exposure_path, stats=("mean",), nodata=-9999)["mean"]
        # No exposure coverage → treat as flat/neutral rather than penalize.
        scores = [WIND_NO_DATA_SCORE if np.isnan(m) else band_score(m, EXPOSURE_TPI_THRESHOLDS)
                  for m in means]
        logger.info("Wind: no wind raster; scored from terrain exposure (TPI) proxy")
        return pd.Series(scores, index=candidates.index, dtype=float)

    logger.warning("No wind or exposure raster; wind scores = 0")
    return pd.Series(0.0, index=candidates.index)

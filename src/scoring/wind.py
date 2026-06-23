"""Wind scoring — Global Wind Atlas resource when available, else exposure proxy.

Wind is a conditional winter-redundancy bonus in NS, not a core driver. When a
Global Wind Atlas raster (mean wind speed at hub height) is present in
``processed/wind.tif`` it is used directly; otherwise terrain exposure (the
Topographic Position Index raster) is a lower-confidence proxy — a turbine
wants an exposed ridge/hilltop, not a sheltered valley.
"""

import logging

import geopandas as gpd
import pandas as pd

from src.config import Config
from src.constants import EXPOSURE_TPI_THRESHOLDS, WIND_SPEED_THRESHOLDS
from src.scoring.registry import register

logger = logging.getLogger("property_finder")


def _lookup_score(value: float, thresholds: list[tuple]) -> int:
    for low, high, score in thresholds:
        if low <= value < high:
            return score
    return 0


def wind_uses_proxy(config: Config) -> bool:
    """True when wind is scored from terrain exposure rather than a wind raster."""
    return not (config.paths.processed / "wind.tif").exists()


@register("wind")
def score_wind(candidates: gpd.GeoDataFrame, config: Config) -> pd.Series:
    """Score cells by wind resource (GWA raster) or terrain exposure (TPI proxy)."""
    wind_path = config.paths.processed / "wind.tif"
    exposure_path = config.paths.processed / "exposure.tif"

    try:
        from rasterstats import zonal_stats
    except ImportError:
        logger.warning("rasterstats not installed; wind scores = 0")
        return pd.Series(0.0, index=candidates.index)

    if wind_path.exists():
        stats = zonal_stats(candidates.geometry, str(wind_path), stats=["mean"])
        scores = [_lookup_score(s.get("mean") or 0.0, WIND_SPEED_THRESHOLDS) for s in stats]
        logger.info("Wind: scored from Global Wind Atlas raster")
        return pd.Series(scores, index=candidates.index, dtype=float)

    if exposure_path.exists():
        stats = zonal_stats(candidates.geometry, str(exposure_path), stats=["mean"], nodata=-9999)
        scores = []
        for s in stats:
            tpi = s.get("mean")
            # No exposure coverage → treat as flat/neutral rather than penalize.
            scores.append(40 if tpi is None else _lookup_score(tpi, EXPOSURE_TPI_THRESHOLDS))
        logger.info("Wind: no wind raster; scored from terrain exposure (TPI) proxy")
        return pd.Series(scores, index=candidates.index, dtype=float)

    logger.warning("No wind or exposure raster; wind scores = 0")
    return pd.Series(0.0, index=candidates.index)

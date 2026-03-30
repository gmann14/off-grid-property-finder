"""Buildable/open-area scoring based on buildability mask percentage."""

import logging
from pathlib import Path

import geopandas as gpd
import pandas as pd

from src.config import Config
from src.constants import BUILDABLE_PERCENT_THRESHOLDS
from src.scoring.registry import register

logger = logging.getLogger("property_finder")


def _lookup_score(value: float, thresholds: list[tuple]) -> int:
    for low, high, score in thresholds:
        if low <= value < high:
            return score
    return 0


@register("buildable")
def score_buildable(candidates: gpd.GeoDataFrame, config: Config) -> pd.Series:
    """Score cells by percentage of buildable area from the buildability mask."""
    mask_path = config.paths.processed / "buildability_mask.tif"

    if not mask_path.exists():
        logger.warning("Buildability mask not found; buildable scores = 0")
        return pd.Series(0.0, index=candidates.index)

    try:
        from rasterstats import zonal_stats
    except ImportError:
        logger.warning("rasterstats not installed; buildable scores = 0")
        return pd.Series(0.0, index=candidates.index)

    stats = zonal_stats(
        candidates.geometry,
        str(mask_path),
        stats=["mean"],
        nodata=255,
    )

    scores = []
    for stat in stats:
        mean_val = stat.get("mean")
        if mean_val is None:
            scores.append(0)
        else:
            # mean of 0/1 mask = fraction buildable
            pct = mean_val * 100
            scores.append(_lookup_score(pct, BUILDABLE_PERCENT_THRESHOLDS))

    return pd.Series(scores, index=candidates.index, dtype=float)

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
from rasterio.windows import from_bounds

from src.config import Config
from src.constants import (
    OPEN_GROUND_PERCENT_THRESHOLDS,
    OPEN_GROUND_SOUTH_BONUS,
    OPEN_GROUND_SOUTH_FRACTION,
    SOLAR_ACCEPTABLE_ASPECT,
)
from src.scoring.registry import register

logger = logging.getLogger("property_finder")


def _lookup_score(value: float, thresholds: list[tuple]) -> int:
    for low, high, score in thresholds:
        if low <= value < high:
            return score
    return 0


def _south_fractions(candidates: gpd.GeoDataFrame, aspect_path) -> list[float]:
    """Fraction of each cell facing roughly south (acceptable solar aspect)."""
    fractions = [0.0] * len(candidates)
    if not aspect_path.exists():
        return fractions
    acc_lo, acc_hi = SOLAR_ACCEPTABLE_ASPECT
    with rasterio.open(aspect_path) as asp:
        nd = asp.nodata
        for j, geom in enumerate(candidates.geometry):
            try:
                win = from_bounds(*geom.bounds, asp.transform)
                a = asp.read(1, window=win)
                valid = a != nd if nd is not None else np.ones_like(a, dtype=bool)
                a = a[valid]
                if a.size == 0:
                    continue
                fractions[j] = float(((a >= acc_lo) & (a <= acc_hi)).mean())
            except Exception:
                continue
    return fractions


@register("open_ground")
def score_open_ground(candidates: gpd.GeoDataFrame, config: Config) -> pd.Series:
    """Score cells by usable open, buildable ground area (+ small south bonus)."""
    mask_path = config.paths.processed / "buildability_mask.tif"
    aspect_path = config.paths.processed / "aspect.tif"

    if not mask_path.exists():
        logger.warning("Buildability mask not found; open_ground scores = 0")
        return pd.Series(0.0, index=candidates.index)

    try:
        from rasterstats import zonal_stats
    except ImportError:
        logger.warning("rasterstats not installed; open_ground scores = 0")
        return pd.Series(0.0, index=candidates.index)

    # Buildable mask is 0/1 (nodata 255) → mean = fraction of cell that is
    # open, flat, unbuilt, non-water ground.
    stats = zonal_stats(candidates.geometry, str(mask_path), stats=["mean"], nodata=255)
    pct = [((s.get("mean") or 0.0) * 100) for s in stats]
    base = [_lookup_score(p, OPEN_GROUND_PERCENT_THRESHOLDS) for p in pct]

    south = _south_fractions(candidates, aspect_path)

    scores = [
        min(100.0, b + (OPEN_GROUND_SOUTH_BONUS if frac >= OPEN_GROUND_SOUTH_FRACTION else 0.0))
        for b, frac in zip(base, south)
    ]
    return pd.Series(scores, index=candidates.index, dtype=float)

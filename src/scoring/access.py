"""Access scoring — road proximity and civic address presence."""

import logging
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point
from shapely.strtree import STRtree

from src.config import Config
from src.constants import ACCESS_DISTANCE_THRESHOLDS
from src.scoring.registry import register

logger = logging.getLogger("property_finder")


def _lookup_score(value: float, thresholds: list[tuple]) -> int:
    for low, high, score in thresholds:
        if low == high and value == low:
            return score  # Exact match (e.g., distance == 0)
        if low <= value < high:
            return score
    return 0


def _compute_min_distances(
    candidates: gpd.GeoDataFrame,
    features: gpd.GeoDataFrame,
    max_dist: float = 500.0,
) -> pd.Series:
    """Compute minimum distance from each cell centroid to nearest feature.

    Uses spatial index for efficiency. Returns 0 if a feature intersects the cell.
    """
    centroids = candidates.geometry.centroid

    # Build spatial index on features
    tree = STRtree(features.geometry.values)

    distances = []
    for idx, (centroid, cell_geom) in enumerate(zip(centroids, candidates.geometry)):
        # Check intersection first (buffer by 0 to handle edge cases)
        nearby_idxs = tree.query(cell_geom, predicate="intersects")
        if len(nearby_idxs) > 0:
            distances.append(0.0)
            continue

        # Query within max_dist buffer
        buffered = centroid.buffer(max_dist)
        nearby_idxs = tree.query(buffered, predicate="intersects")
        if len(nearby_idxs) == 0:
            distances.append(float("inf"))
            continue

        min_d = min(
            centroid.distance(features.geometry.iloc[i])
            for i in nearby_idxs
        )
        distances.append(min_d)

    return pd.Series(distances, index=candidates.index)


@register("access")
def score_access(candidates: gpd.GeoDataFrame, config: Config) -> pd.Series:
    """Score cells by distance to nearest road or civic address.

    Uses spatial indexing for efficient nearest-feature queries.
    """
    roads_path = config.paths.processed / "roads.gpkg"
    civic_path = config.paths.processed / "civic.gpkg"

    roads = None
    civic = None

    if roads_path.exists():
        roads = gpd.read_file(roads_path)
        if roads.crs and str(roads.crs) != config.working_crs:
            roads = roads.to_crs(config.working_crs)
        # Filter to vehicle-accessible roads only
        if "highway" in roads.columns:
            non_vehicle = {"footway", "path", "steps", "cycleway", "proposed", "construction"}
            n_before = len(roads)
            roads = roads[~roads["highway"].isin(non_vehicle)]
            logger.info("Filtered to %d vehicle roads (from %d)", len(roads), n_before)

    if civic_path.exists():
        civic = gpd.read_file(civic_path)
        if civic.crs and str(civic.crs) != config.working_crs:
            civic = civic.to_crs(config.working_crs)

    if roads is None and civic is None:
        logger.warning("No road or civic data; access scores = 0")
        return pd.Series(0.0, index=candidates.index)

    # Compute distances to roads
    min_distances = pd.Series(float("inf"), index=candidates.index)

    if roads is not None and not roads.empty:
        road_dists = _compute_min_distances(candidates, roads)
        min_distances = min_distances.combine(road_dists, min)

    # Compute distances to civic addresses
    if civic is not None and not civic.empty:
        civic_dists = _compute_min_distances(candidates, civic)
        min_distances = min_distances.combine(civic_dists, min)

    scores = min_distances.apply(lambda d: _lookup_score(d, ACCESS_DISTANCE_THRESHOLDS))
    return scores.astype(float)

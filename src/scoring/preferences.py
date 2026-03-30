"""Optional preference metrics (Stage B and bonus scoring)."""

import logging
from pathlib import Path

import geopandas as gpd
import pandas as pd

from src.config import Config
from src.constants import PARCEL_SIZE_THRESHOLDS

logger = logging.getLogger("property_finder")


def _lookup_score(value: float, thresholds: list[tuple]) -> int:
    for low, high, score in thresholds:
        if low <= value < high:
            return score
    return 0


def score_parcel_size(parcels: gpd.GeoDataFrame) -> pd.Series:
    """Score parcels by area in acres."""
    if "area_acres" not in parcels.columns:
        # Calculate area in acres from geometry (assumes projected CRS in meters)
        area_m2 = parcels.geometry.area
        parcels = parcels.copy()
        parcels["area_acres"] = area_m2 / 4046.86

    return pd.Series(
        [_lookup_score(a, PARCEL_SIZE_THRESHOLDS) for a in parcels["area_acres"]],
        index=parcels.index,
        dtype=float,
    )


def aggregate_to_parcels(
    candidates: gpd.GeoDataFrame,
    parcels: gpd.GeoDataFrame,
    top_n: int = 3,
    cell_weight: float = 0.8,
) -> gpd.GeoDataFrame:
    """Aggregate cell scores to parcels (Stage B).

    Each parcel's score = cell_weight * mean(top N eligible cell scores)
                        + (1 - cell_weight) * parcel_size_score

    Cells are assigned to parcels by centroid containment.
    """
    parcels = parcels.copy()

    # Assign cells to parcels by centroid
    centroids = candidates.geometry.centroid
    centroid_gdf = gpd.GeoDataFrame(
        {"cell_idx": candidates.index, "score": candidates["score"]},
        geometry=centroids,
        crs=candidates.crs,
    )

    # Only eligible cells
    eligible_mask = candidates.get("status") != "excluded"
    if eligible_mask is not None:
        centroid_gdf = centroid_gdf[eligible_mask.values]

    joined = gpd.sjoin(centroid_gdf, parcels, how="left", predicate="within")

    # Compute mean of top N cell scores per parcel
    parcel_scores = {}
    for parcel_idx in parcels.index:
        cells_in = joined[joined["index_right"] == parcel_idx]
        if len(cells_in) == 0:
            parcel_scores[parcel_idx] = None
            continue
        top_scores = cells_in["score"].dropna().nlargest(top_n)
        if len(top_scores) == 0:
            parcel_scores[parcel_idx] = None
        else:
            parcel_scores[parcel_idx] = top_scores.mean()

    parcels["cell_score"] = pd.Series(parcel_scores, dtype=float)

    # Parcel size score
    parcels["size_score"] = score_parcel_size(parcels)

    # Composite parcel score
    parcels["score"] = (
        cell_weight * parcels["cell_score"].fillna(0)
        + (1 - cell_weight) * parcels["size_score"]
    )

    # Mark parcels with no assigned candidates
    no_candidates = parcels["cell_score"].isna()
    parcels.loc[no_candidates, "score"] = None

    assigned = (~no_candidates).sum()
    logger.info(
        "Parcel aggregation: %d of %d parcels have assigned cells",
        assigned, len(parcels),
    )
    return parcels

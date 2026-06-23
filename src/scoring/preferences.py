"""Optional preference metrics (Stage B and bonus scoring)."""

import logging
from pathlib import Path

import geopandas as gpd
import pandas as pd

from src.config import Config
from src.constants import (
    FLAG_PARCEL_NO_CANDIDATES,
    FLAG_SEVERANCE_CANDIDATE,
    PARCEL_CELL_WEIGHT,
    PARCEL_LIGHT_MAX_BUILDINGS,
    PARCEL_SIZE_THRESHOLDS,
    PARCEL_TOP_N_CELLS,
    PARCEL_TYPE_DEVELOPED,
    PARCEL_TYPE_LAND_ONLY,
    PARCEL_TYPE_LIGHTLY_BUILT,
    SEVERANCE_MAX_BUILDINGS,
    SEVERANCE_MIN_ACRES,
    SEVERANCE_MIN_CELL_SCORE,
)

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
    top_n: int = PARCEL_TOP_N_CELLS,
    cell_weight: float = PARCEL_CELL_WEIGHT,
) -> gpd.GeoDataFrame:
    """Aggregate cell scores to parcels (Stage B).

    Each parcel's score = cell_weight * mean(top N eligible cell scores)
                        + (1 - cell_weight) * parcel_size_score

    Cells are assigned to parcels by centroid containment. Identifier columns
    (``PID``, ``AAN``) pass through untouched so the ranked output is keyed by
    real property identifiers. Adds ``n_cells``, ``rank`` (over scored parcels),
    and a ``flags`` column marking parcels with no assigned candidates.
    """
    # reset_index so the sjoin index_right -> parcels.index.map alignment can't
    # be corrupted by a duplicate/non-default parcel index.
    parcels = parcels.reset_index(drop=True).copy()

    # Assign eligible-cell centroids to parcels (single spatial join, grouped).
    eligible = candidates
    if "status" in candidates.columns:
        eligible = candidates[candidates["status"] != "excluded"]

    centroid_gdf = gpd.GeoDataFrame(
        {"score": eligible["score"].values},
        geometry=eligible.geometry.centroid.values,
        crs=candidates.crs,
    )
    joined = gpd.sjoin(
        centroid_gdf, parcels[["geometry"]], how="inner", predicate="within"
    )

    scored = joined.dropna(subset=["score"])
    grouped = scored.groupby("index_right")["score"]
    cell_score = grouped.apply(lambda s: s.nlargest(top_n).mean())
    n_cells = grouped.size()

    parcels["n_cells"] = parcels.index.map(n_cells).fillna(0).astype(int)
    parcels["cell_score"] = parcels.index.map(cell_score).astype(float)

    # Parcel size score (preference component)
    parcels["size_score"] = score_parcel_size(parcels)

    # Composite parcel score; parcels with no candidate cells score null
    parcels["score"] = (
        cell_weight * parcels["cell_score"].fillna(0)
        + (1 - cell_weight) * parcels["size_score"]
    )
    no_candidates = parcels["cell_score"].isna()
    parcels.loc[no_candidates, "score"] = None

    # Rank scored parcels (best = 1), and flag empty ones
    parcels["rank"] = (
        parcels["score"].rank(ascending=False, method="min").astype("Int64")
    )
    parcels["flags"] = ""
    parcels.loc[no_candidates, "flags"] = FLAG_PARCEL_NO_CANDIDATES

    assigned = int((~no_candidates).sum())
    logger.info(
        "Parcel aggregation: %d of %d parcels have assigned cells",
        assigned, len(parcels),
    )
    return parcels


def classify_parcels(
    parcels: gpd.GeoDataFrame,
    buildings: gpd.GeoDataFrame | None,
) -> gpd.GeoDataFrame:
    """Type parcels as land-only / lightly-built / developed and flag severance
    candidates (Stage B).

    Adds ``n_buildings``, ``parcel_type``, and a ``severance_candidate`` boolean
    for big, already-developed parcels whose resource-bearing land could plausibly
    be split off — the basis for the owner-outreach workflow. Requires
    ``cell_score`` and ``area_acres`` (from aggregate_to_parcels); both optional.
    """
    parcels = parcels.reset_index(drop=True).copy()

    # Count buildings whose centroid falls within each parcel
    n_buildings = pd.Series(0, index=parcels.index, dtype=int)
    if buildings is not None and not buildings.empty:
        if str(buildings.crs) != str(parcels.crs):
            buildings = buildings.to_crs(parcels.crs)
        centroids = gpd.GeoDataFrame(geometry=buildings.geometry.centroid, crs=parcels.crs)
        joined = gpd.sjoin(centroids, parcels[["geometry"]], how="inner", predicate="within")
        counts = joined.groupby("index_right").size()
        n_buildings = parcels.index.map(counts).fillna(0).astype(int)
    parcels["n_buildings"] = n_buildings

    def _type(n: int) -> str:
        if n == 0:
            return PARCEL_TYPE_LAND_ONLY
        if n <= PARCEL_LIGHT_MAX_BUILDINGS:
            return PARCEL_TYPE_LIGHTLY_BUILT
        return PARCEL_TYPE_DEVELOPED

    parcels["parcel_type"] = parcels["n_buildings"].map(_type)

    acres = parcels["area_acres"] if "area_acres" in parcels.columns else pd.Series(0.0, index=parcels.index)
    cell_score = parcels["cell_score"] if "cell_score" in parcels.columns else pd.Series(0.0, index=parcels.index)
    parcels["severance_candidate"] = (
        (parcels["n_buildings"] >= 1)
        & (parcels["n_buildings"] <= SEVERANCE_MAX_BUILDINGS)
        & (acres >= SEVERANCE_MIN_ACRES)
        & (cell_score.fillna(0) >= SEVERANCE_MIN_CELL_SCORE)
    )

    # Append the severance flag to the parcel flags column
    if "flags" not in parcels.columns:
        parcels["flags"] = ""
    sev = parcels["severance_candidate"]
    parcels.loc[sev, "flags"] = (
        parcels.loc[sev, "flags"].replace("", pd.NA).fillna(FLAG_SEVERANCE_CANDIDATE)
    )
    parcels.loc[sev & (parcels["flags"] != FLAG_SEVERANCE_CANDIDATE), "flags"] = (
        parcels.loc[sev & (parcels["flags"] != FLAG_SEVERANCE_CANDIDATE), "flags"]
        + "; " + FLAG_SEVERANCE_CANDIDATE
    )

    logger.info(
        "Parcel typing: %d land-only, %d lightly-built, %d developed; %d severance candidates",
        int((parcels["parcel_type"] == PARCEL_TYPE_LAND_ONLY).sum()),
        int((parcels["parcel_type"] == PARCEL_TYPE_LIGHTLY_BUILT).sum()),
        int((parcels["parcel_type"] == PARCEL_TYPE_DEVELOPED).sum()),
        int(parcels["severance_candidate"].sum()),
    )
    return parcels

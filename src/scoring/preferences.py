"""Optional preference metrics (Stage B and bonus scoring)."""

import logging
from pathlib import Path

import geopandas as gpd
import pandas as pd

from src.config import Config
from src.constants import (
    FLAG_PARCEL_BELOW_MIN_AREA,
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
    SQ_M_PER_ACRE,
)

logger = logging.getLogger("property_finder")


def _lookup_score(value: float, thresholds: list[tuple]) -> int:
    for low, high, score in thresholds:
        if low <= value < high:
            return score
    return 0


def _scoring_acres(parcels: gpd.GeoDataFrame) -> pd.Series:
    """The acreage the size score and severance rule use: ``land_acres`` (the
    parcel less any water in it; see src.exclusions.parcel_land_acres) where
    present, else ``area_acres``, else the mapped area (projected CRS in m)."""
    if "land_acres" in parcels.columns:
        return parcels["land_acres"].astype(float)
    if "area_acres" in parcels.columns:
        return parcels["area_acres"].astype(float)
    return parcels.geometry.area / SQ_M_PER_ACRE


def score_parcel_size(parcels: gpd.GeoDataFrame) -> pd.Series:
    """Score parcels by land acres (see ``_scoring_acres``)."""
    return pd.Series(
        [_lookup_score(a, PARCEL_SIZE_THRESHOLDS) for a in _scoring_acres(parcels)],
        index=parcels.index,
        dtype=float,
    )


def aggregate_to_parcels(
    candidates: gpd.GeoDataFrame,
    parcels: gpd.GeoDataFrame,
    top_n: int = PARCEL_TOP_N_CELLS,
    cell_weight: float = PARCEL_CELL_WEIGHT,
    assign_points: gpd.GeoSeries | None = None,
    min_area_acres: float = 0.0,
) -> gpd.GeoDataFrame:
    """Aggregate cell scores to parcels (Stage B).

    Each parcel's score = cell_weight * mean(top N eligible cell scores)
                        + (1 - cell_weight) * parcel_size_score

    The size score uses ``land_acres`` when the parcels carry it (see
    ``src.exclusions.parcel_land_acres``), else ``area_acres``.

    Cells are assigned to parcels by centroid containment, or by
    ``assign_points`` (indexed like ``candidates``; see
    ``src.exclusions.parcel_assignment_points``) where given. Identifier columns
    (``PID``, ``AAN``) pass through untouched so the ranked output is keyed by
    real property identifiers. Adds ``n_cells``, ``rank`` (over scored parcels),
    and a ``flags`` column marking parcels with no assigned candidates.

    A parcel with less than ``min_area_acres`` of land (``config.yaml``
    ``min_parcel_area_acres``; the SPEC's Stage B hard exclusion) keeps its
    ``cell_score`` but gets no score or rank, and is flagged
    ``parcel_below_min_area``.
    """
    # reset_index so the sjoin index_right -> parcels.index.map alignment can't
    # be corrupted by a duplicate/non-default parcel index.
    parcels = parcels.reset_index(drop=True).copy()

    # Assign eligible-cell centroids to parcels (single spatial join, grouped).
    eligible = candidates
    if "status" in candidates.columns:
        eligible = candidates[candidates["status"] != "excluded"]

    points = eligible.geometry.centroid
    if assign_points is not None:
        given = assign_points.reindex(eligible.index)
        has_point = given.notna() & ~given.is_empty
        points.loc[has_point] = given[has_point]
    centroid_gdf = gpd.GeoDataFrame(
        {"score": eligible["score"].values},
        geometry=points.values,
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
    # NaN acreage (no geometry) compares False, so it isn't excluded here
    below_min = _scoring_acres(parcels) < min_area_acres
    parcels.loc[no_candidates | below_min, "score"] = None

    # Rank scored parcels (best = 1), and flag the unscored ones
    parcels["rank"] = (
        parcels["score"].rank(ascending=False, method="min").astype("Int64")
    )
    flags = pd.Series([[] for _ in range(len(parcels))], index=parcels.index)
    for mask, flag in ((no_candidates, FLAG_PARCEL_NO_CANDIDATES),
                       (below_min, FLAG_PARCEL_BELOW_MIN_AREA)):
        for i in parcels.index[mask]:
            flags[i].append(flag)
    parcels["flags"] = flags.map("; ".join)

    assigned = int((~no_candidates).sum())
    logger.info(
        "Parcel aggregation: %d of %d parcels have assigned cells; %d of those "
        "have under %.2f acres of land and are left unranked",
        assigned, len(parcels), int((~no_candidates & below_min).sum()), min_area_acres,
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
    be split off — the basis for the owner-outreach workflow. Uses
    ``cell_score`` (from aggregate_to_parcels) and ``land_acres`` (falling back
    to ``area_acres``); all optional.
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

    # Land acres, not the deeded area: lake or sea in the lot can't be split off.
    acres = (_scoring_acres(parcels)
             if {"land_acres", "area_acres"} & set(parcels.columns)
             else pd.Series(0.0, index=parcels.index))
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

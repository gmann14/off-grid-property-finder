"""Optional preference metrics (Stage B and bonus scoring)."""

import logging

import geopandas as gpd
import pandas as pd

from src.constants import (
    FLAG_PARCEL_BELOW_MIN_AREA,
    FLAG_PARCEL_NO_CANDIDATES,
    FLAG_PARCEL_NOT_PROPERTY,
    FLAG_SEVERANCE_CANDIDATE,
    PARCEL_CELL_WEIGHT,
    PARCEL_LIGHT_MAX_BUILDINGS,
    PARCEL_PLACEHOLDER_PID_PATTERN,
    PARCEL_ROADWAT_NOT_PROPERTY,
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
from src.scoring.bands import band_score
from src.scoring.confidence import confidence_band, order_flags

logger = logging.getLogger("property_finder")


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
        [band_score(a, PARCEL_SIZE_THRESHOLDS) for a in _scoring_acres(parcels)],
        index=parcels.index,
        dtype=float,
    )


def not_property_mask(parcels: pd.DataFrame) -> pd.Series:
    """True for parcel polygons that are not a property anyone can buy: NSPRD
    road rights-of-way and water lots (their ``ROADWAT`` code, see
    ``PARCEL_ROADWAT_NOT_PROPERTY``) and placeholder PIDs such as "00000006".
    Missing columns mean nothing is masked."""
    mask = pd.Series(False, index=parcels.index)
    if "ROADWAT" in parcels.columns:
        mask |= parcels["ROADWAT"].astype("string").str.strip().isin(
            list(PARCEL_ROADWAT_NOT_PROPERTY)).fillna(False).astype(bool)
    if "PID" in parcels.columns:
        mask |= parcels["PID"].astype("string").str.fullmatch(
            PARCEL_PLACEHOLDER_PID_PATTERN).fillna(False).astype(bool)
    return mask


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

    Polygons that are not property (see ``not_property_mask``: road
    rights-of-way, water lots, placeholder PIDs) are likewise kept unranked and
    flagged ``parcel_not_property``.
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
    # Per-criterion cell scores (score_hydro, ...), not the composite or the
    # all-rounder, travel with each cell so the top-N can be broken down.
    criteria = [c for c in eligible.columns
                if c.startswith("score_") and c != "score_allrounder"]
    carry = ["score"] + criteria + [c for c in ("confidence", "flags") if c in eligible.columns]
    centroid_gdf = gpd.GeoDataFrame(
        {c: eligible[c].values for c in carry},
        geometry=points.values,
        crs=candidates.crs,
    )
    joined = gpd.sjoin(
        centroid_gdf, parcels[["geometry"]], how="inner", predicate="within"
    )

    # One row per (cell, parcel) pair: a cell inside two overlapping parcels
    # counts for both, so rows need their own id.
    scored = joined.dropna(subset=["score"]).reset_index(drop=True)
    grouped = scored.groupby("index_right")["score"]
    cell_score = grouped.apply(lambda s: s.nlargest(top_n).mean())
    n_cells = grouped.size()

    parcels["n_cells"] = parcels.index.map(n_cells).fillna(0).astype(int)
    parcels["cell_score"] = parcels.index.map(cell_score).astype(float)

    # The same top-N cells (nlargest's pick: ties go to the earlier cell, which
    # a stable descending sort keeps first) give the per-criterion means, which
    # add up to cell_score (cell_score = Σ w·top_c), and the confidence and
    # flags the parcel rests on (SPEC §4.4).
    top = (scored.sort_values("score", ascending=False, kind="stable")
           .groupby("index_right", sort=False).head(top_n).groupby("index_right"))
    for col in criteria:
        parcels[f"top_{col.removeprefix('score_')}"] = (
            parcels.index.map(top[col].mean()).astype(float))
    if "confidence" in scored.columns:
        parcels["confidence"] = parcels.index.map(top["confidence"].min()).astype(float)
        parcels["confidence_band"] = confidence_band(parcels["confidence"])
    cell_flags = pd.Series(dtype=object)
    if "flags" in scored.columns:
        cell_flags = top["flags"].agg(
            lambda s: order_flags(f for v in s.dropna() for f in str(v).split("; ") if f))

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
    not_property = not_property_mask(parcels)
    parcels.loc[no_candidates | below_min | not_property, "score"] = None

    # Rank scored parcels (best = 1), and flag the unscored ones
    parcels["rank"] = (
        parcels["score"].rank(ascending=False, method="min").astype("Int64")
    )
    flags = pd.Series([[] for _ in range(len(parcels))], index=parcels.index)
    for mask, flag in ((no_candidates, FLAG_PARCEL_NO_CANDIDATES),
                       (below_min, FLAG_PARCEL_BELOW_MIN_AREA),
                       (not_property, FLAG_PARCEL_NOT_PROPERTY)):
        for i in parcels.index[mask]:
            flags[i].append(flag)
    # then the flags of the top-N cells the parcel score rests on
    for i, cell in cell_flags.items():
        flags[i].extend(cell)
    parcels["flags"] = flags.map("; ".join)

    assigned = int((~no_candidates).sum())
    logger.info(
        "Parcel aggregation: %d of %d parcels have assigned cells; %d of those "
        "have under %.2f acres of land and %d are not property (road, water lot or "
        "placeholder PID); both are left unranked",
        assigned, len(parcels), int((~no_candidates & below_min).sum()), min_area_acres,
        int((~no_candidates & not_property).sum()),
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
        ~not_property_mask(parcels)
        & (parcels["n_buildings"] >= 1)
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

"""Exclusion layer preparation and application."""

import logging
from pathlib import Path

import geopandas as gpd
from shapely.geometry import box

from src.config import Config

logger = logging.getLogger("property_finder")


def load_exclusions(
    protected_path: Path | None,
    flood_path: Path | None,
    working_crs: str,
    study_bbox: tuple[float, float, float, float],
) -> gpd.GeoDataFrame:
    """Load and merge exclusion layers into a single GeoDataFrame with reason tags."""
    exclusions = []

    if protected_path and protected_path.exists():
        gdf = gpd.read_file(protected_path)
        if str(gdf.crs) != working_crs:
            gdf = gdf.to_crs(working_crs)
        bbox_geom = box(*study_bbox)
        gdf = gdf[gdf.intersects(bbox_geom)].copy()
        gdf["exclusion_reason"] = "protected_area"
        exclusions.append(gdf[["geometry", "exclusion_reason"]])
        logger.info("Loaded protected areas: %d features", len(gdf))

    if flood_path and flood_path.exists():
        gdf = gpd.read_file(flood_path)
        if str(gdf.crs) != working_crs:
            gdf = gdf.to_crs(working_crs)
        bbox_geom = box(*study_bbox)
        gdf = gdf[gdf.intersects(bbox_geom)].copy()
        gdf["exclusion_reason"] = "flood_zone"
        exclusions.append(gdf[["geometry", "exclusion_reason"]])
        logger.info("Loaded flood zones: %d features", len(gdf))

    if not exclusions:
        logger.warning("No exclusion layers loaded")
        return gpd.GeoDataFrame(columns=["geometry", "exclusion_reason"], crs=working_crs)

    merged = gpd.GeoDataFrame(gpd.pd.concat(exclusions, ignore_index=True), crs=working_crs)
    logger.info("Total exclusion features: %d", len(merged))
    return merged


def apply_exclusions(
    candidates: gpd.GeoDataFrame,
    exclusions: gpd.GeoDataFrame,
    overlap_threshold: float = 0.5,
) -> gpd.GeoDataFrame:
    """Apply hard exclusions to candidate cells.

    A cell is excluded when:
    - Its centroid falls inside an exclusion polygon, OR
    - The overlap fraction exceeds the configured threshold.

    Returns the candidates GeoDataFrame with 'status' and 'exclusion_reasons' columns.
    """
    candidates = candidates.copy()
    candidates["status"] = "eligible"
    candidates["exclusion_reasons"] = ""

    if exclusions.empty:
        return candidates

    centroids = candidates.geometry.centroid
    centroid_gdf = gpd.GeoDataFrame(
        {"cell_id": candidates.index}, geometry=centroids, crs=candidates.crs
    )

    # Check centroid containment
    centroid_joins = gpd.sjoin(centroid_gdf, exclusions, how="left", predicate="within")
    excluded_by_centroid = centroid_joins.dropna(subset=["exclusion_reason"])

    for idx in excluded_by_centroid.index.unique():
        reasons = excluded_by_centroid.loc[[idx], "exclusion_reason"].unique()
        candidates.loc[idx, "status"] = "excluded"
        candidates.loc[idx, "exclusion_reasons"] = "; ".join(reasons)

    # Check overlap fraction for cells not already excluded
    eligible_mask = candidates["status"] == "eligible"
    if eligible_mask.any() and not exclusions.empty:
        eligible = candidates[eligible_mask]
        for idx, cell in eligible.iterrows():
            cell_area = cell.geometry.area
            if cell_area == 0:
                continue
            overlap = exclusions.intersection(cell.geometry)
            overlap_area = overlap.area.sum()
            if overlap_area / cell_area >= overlap_threshold:
                reasons = exclusions[overlap.area > 0]["exclusion_reason"].unique()
                candidates.loc[idx, "status"] = "excluded"
                candidates.loc[idx, "exclusion_reasons"] = "; ".join(reasons)

    excluded_count = (candidates["status"] == "excluded").sum()
    logger.info("Excluded %d of %d candidate cells", excluded_count, len(candidates))
    return candidates

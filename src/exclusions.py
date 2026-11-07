"""Exclusion layer preparation and application."""

import logging
from pathlib import Path

import geopandas as gpd
import shapely
from shapely.geometry import box

from src.config import Config

logger = logging.getLogger("property_finder")


# Reasons decided by overlap fraction only, never by centroid containment. A
# cell whose centre sits just inside a lake but is mostly shore is still land,
# and lake outlets (real hydro head) are exactly where that happens: on the
# Lunenburg data, 728 cells have their centroid in water but are under half
# water (median 34%); 605 of them are otherwise eligible, and 99 of those
# score hydro >= 60.
_OVERLAP_ONLY_REASONS = frozenset({"water"})


def _polygonal(geom):
    """The polygonal part of ``geom`` (None if it has none). make_valid can
    return a GeometryCollection with stray lines/points from a bad ring."""
    if geom is None or geom.is_empty:
        return None
    if geom.geom_type in ("Polygon", "MultiPolygon"):
        return geom
    parts = [g for g in shapely.get_parts(geom) if g.geom_type in ("Polygon", "MultiPolygon")]
    return shapely.union_all(parts) if parts else None


def _load_layer(path: Path | None, reason: str, working_crs: str,
                study_bbox: tuple[float, float, float, float]) -> gpd.GeoDataFrame | None:
    if not path or not path.exists():
        return None
    gdf = gpd.read_file(path)
    if str(gdf.crs) != working_crs:
        gdf = gdf.to_crs(working_crs)
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]
    invalid = ~gdf.is_valid
    if invalid.any():
        # An invalid ring (self-intersection) can make the overlap
        # intersection raise or return the wrong area.
        gdf = gdf.copy()
        gdf.loc[invalid, "geometry"] = gdf.loc[invalid].geometry.make_valid().apply(_polygonal)
        gdf = gdf[gdf.geometry.notna()]
        logger.info("Repaired %d invalid %s polygon(s)", int(invalid.sum()), reason)
    gdf = gdf[gdf.intersects(box(*study_bbox))].copy()
    gdf["exclusion_reason"] = reason
    logger.info("Loaded %s: %d features", reason, len(gdf))
    return gdf[["geometry", "exclusion_reason"]]


def load_exclusions(
    protected_path: Path | None,
    flood_path: Path | None,
    working_crs: str,
    study_bbox: tuple[float, float, float, float],
    water_path: Path | None = None,
) -> gpd.GeoDataFrame:
    """Load and merge exclusion layers into a single GeoDataFrame with reason tags.

    ``water_path`` (processed/waterbodies.gpkg: lakes, rivers and the sea) is
    tagged ``"water"``, which `apply_exclusions` decides by overlap only. Any
    layer whose path is None or missing is skipped.
    """
    exclusions = [
        layer for layer in (
            _load_layer(protected_path, "protected_area", working_crs, study_bbox),
            _load_layer(flood_path, "flood_zone", working_crs, study_bbox),
            _load_layer(water_path, "water", working_crs, study_bbox),
        )
        if layer is not None
    ]

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
    - Its centroid falls inside an exclusion polygon (except "water", which is
      decided by overlap only; see ``_OVERLAP_ONLY_REASONS``), OR
    - The fraction of the cell covered by exclusion polygons, counting any
      area covered by several polygons once, reaches the configured threshold.

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
    by_centroid = exclusions[~exclusions["exclusion_reason"].isin(_OVERLAP_ONLY_REASONS)]
    centroid_joins = gpd.sjoin(centroid_gdf, by_centroid, how="left", predicate="within")
    excluded_by_centroid = centroid_joins.dropna(subset=["exclusion_reason"])

    for idx in excluded_by_centroid.index.unique():
        reasons = excluded_by_centroid.loc[[idx], "exclusion_reason"].unique()
        candidates.loc[idx, "status"] = "excluded"
        candidates.loc[idx, "exclusion_reasons"] = "; ".join(reasons)

    # Check overlap fraction for cells not already excluded.
    #
    # Previously this intersected every eligible cell against every exclusion
    # polygon (O(cells x exclusion_features) — e.g. ~40k cells x ~2k flood
    # polygons). A spatial join (STRtree-accelerated) first narrows this down
    # to only the cell/exclusion pairs that actually touch — cells fully
    # inside or fully outside an exclusion never reach the exact-area step —
    # then exact intersection area is computed once per matched pair via a
    # vectorized elementwise GeoSeries.intersection(), not a per-cell loop.
    eligible_mask = candidates["status"] == "eligible"
    if eligible_mask.any():
        eligible = candidates.loc[eligible_mask, ["geometry"]]
        pairs = gpd.sjoin(eligible, exclusions, how="inner", predicate="intersects")
        if not pairs.empty:
            left = candidates.geometry.reindex(pairs.index)
            right = exclusions.geometry.reindex(pairs["index_right"].values)
            right.index = pairs.index
            pieces = left.intersection(right)
            pairs = pairs.assign(_overlap_area=pieces.area.values)
            # Covered area per cell. Summing the pieces would count twice any
            # area under two polygons (e.g. sea that is also a mapped flood
            # zone), so cells touched by more than one polygon use the area of
            # the union of their pieces instead.
            covered = pairs["_overlap_area"].groupby(level=0).sum()
            multi = pairs.index[pairs.index.duplicated()].unique()
            for idx in multi:
                covered[idx] = shapely.union_all(pieces.loc[[idx]].values).area

            for idx, group in pairs.groupby(pairs.index):
                cell_area = candidates.geometry.loc[idx].area
                if cell_area == 0:
                    continue
                if covered[idx] / cell_area >= overlap_threshold:
                    reasons = group.loc[group["_overlap_area"] > 0, "exclusion_reason"].unique()
                    candidates.loc[idx, "status"] = "excluded"
                    candidates.loc[idx, "exclusion_reasons"] = "; ".join(reasons)

    excluded = candidates["status"] == "excluded"
    by_reason = candidates.loc[excluded, "exclusion_reasons"].str.split("; ").explode().value_counts()
    logger.info("Excluded %d of %d candidate cells (%s)", int(excluded.sum()), len(candidates),
                ", ".join(f"{r}: {n}" for r, n in by_reason.items()) or "none")
    return candidates


def parcel_assignment_points(
    cells: gpd.GeoDataFrame,
    exclusions: gpd.GeoDataFrame | None,
    parcels: gpd.GeoDataFrame | None = None,
) -> gpd.GeoSeries:
    """The point that assigns each cell to a parcel.

    Normally the cell's centroid. A cell whose centroid lies in water but that
    is mostly land (water is decided by overlap, so it stays eligible) uses a
    point inside the parcel that holds the largest share of the cell's land
    (or, without ``parcels``, any point on its land part). Otherwise the cell
    goes to whatever parcel holds that bit of water, often a water lot, and
    not to the land parcel it is mostly made of. On the Lunenburg data, 200
    such shore cells (38 with hydro >= 60) were credited to parcels that are at
    least half water, one of which then ranked 2nd on a single land cell plus
    17,000 acres of water.
    """
    points = cells.geometry.centroid
    if exclusions is None or exclusions.empty:
        return points
    water = exclusions[exclusions["exclusion_reason"].isin(_OVERLAP_ONLY_REASONS)]
    if water.empty:
        return points
    in_water = gpd.sjoin(
        gpd.GeoDataFrame(geometry=points, crs=cells.crs), water[["geometry"]],
        how="inner", predicate="within",
    ).index.unique()
    if len(in_water) == 0:
        return points
    tree = shapely.STRtree(water.geometry.values)
    parcel_geoms = parcel_tree = None
    if parcels is not None and not parcels.empty:
        if cells.crs is not None and parcels.crs is not None and parcels.crs != cells.crs:
            parcels = parcels.to_crs(cells.crs)
        parcel_geoms = parcels.geometry.values
        parcel_tree = shapely.STRtree(parcel_geoms)
    moved = 0
    for idx in in_water:
        cell = cells.geometry.loc[idx]
        near = water.geometry.values[tree.query(cell, predicate="intersects")]
        land = cell.difference(shapely.union_all(near))
        if land.is_empty or land.area <= 0:
            continue
        target = land
        if parcel_tree is not None and parcel_geoms is not None:
            hits = parcel_tree.query(land, predicate="intersects")
            if len(hits):
                pieces = shapely.intersection(parcel_geoms[hits], land)
                best = pieces[int(shapely.area(pieces).argmax())]
                if not best.is_empty and best.area > 0:
                    target = best
        points.loc[idx] = target.representative_point()
        moved += 1
    logger.info("Parcel assignment: %d cell(s) with a centroid in water use a point on their land part", moved)
    return points

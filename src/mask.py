"""Mask generation: rural-eligibility mask and buildability mask."""

import logging
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from shapely.geometry import box

from src.config import Config

logger = logging.getLogger("property_finder")


def build_rural_mask(
    land_cover_path: Path | None,
    buildings_path: Path | None,
    study_bbox: tuple[float, float, float, float],
    working_crs: str,
) -> gpd.GeoDataFrame | None:
    """Build a coarse rural-eligibility mask by excluding urban/developed areas.

    Returns a GeoDataFrame of polygons that are considered rural-eligible,
    or None if no land-cover data is available (in which case all cells pass).
    """
    if land_cover_path is None or not land_cover_path.exists():
        logger.warning("No land-cover data available; all cells pass rural-eligibility filter")
        return None

    lc = gpd.read_file(land_cover_path)
    if str(lc.crs) != working_crs:
        lc = lc.to_crs(working_crs)

    # Identify urban/developed features to exclude.
    # NSTDB land cover type field varies; common urban indicators:
    urban_keywords = ["urban", "commercial", "industrial", "institutional", "residential"]
    type_col = None
    for col in lc.columns:
        if col.lower() in (
            "type", "land_type", "landcover", "lc_type", "desc",
            "feature_type", "feat_desc", "feat_code",
        ):
            type_col = col
            break

    if type_col is not None:
        urban_mask = lc[type_col].str.lower().str.contains("|".join(urban_keywords), na=False)
        urban_areas = lc[urban_mask]
    else:
        logger.warning("Could not identify land-cover type column; treating all land as rural")
        return None

    if len(urban_areas) == 0:
        logger.info("No urban areas found in land cover; all cells pass rural-eligibility filter")
        return None

    # The rural mask is the study area minus urban areas
    study_geom = box(*study_bbox)
    study_gdf = gpd.GeoDataFrame(geometry=[study_geom], crs=working_crs)
    rural = gpd.overlay(study_gdf, urban_areas, how="difference")
    logger.info("Built rural-eligibility mask: %d polygons", len(rural))
    return rural


def build_buildability_mask(
    slope_path: Path | None,
    land_cover_path: Path | None,
    water_path: Path | None,
    buildings_path: Path | None,
    study_bbox: tuple[float, float, float, float],
    working_crs: str,
    slope_threshold_deg: float = 20.0,
) -> Path | None:
    """Build a raster mask where 1 = buildable, 0 = constrained.

    Constraints: steep slope, water/wetland, existing buildings, dense forest.
    Returns path to the mask raster, or None if no slope raster is available.
    """
    if slope_path is None or not slope_path.exists():
        logger.warning("No slope raster available; cannot build buildability mask")
        return None

    with rasterio.open(slope_path) as src:
        slope = src.read(1)
        meta = src.meta.copy()
        transform = src.transform

    # Start with slope constraint
    buildable = (slope <= slope_threshold_deg).astype(np.uint8)

    # TODO: overlay water/wetland, buildings, and dense forest when data is available
    # Each would set buildable = 0 where the constraint applies.
    # For MVP, slope is the primary raster-level constraint; vector constraints
    # are applied during scoring via zonal statistics.

    mask_path = slope_path.parent / "buildability_mask.tif"
    meta.update(dtype="uint8", nodata=255)
    with rasterio.open(mask_path, "w", **meta) as dst:
        dst.write(buildable, 1)

    logger.info("Built buildability mask: %s", mask_path)
    return mask_path

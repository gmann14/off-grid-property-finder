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


def _burn_constraint(
    buildable: np.ndarray,
    path: Path | None,
    shape: tuple[int, int],
    transform,
    working_crs: str,
    study_bbox: tuple[float, float, float, float],
    buffer_m: float = 0.0,
    geom_filter=None,
) -> int:
    """Set buildable=0 wherever features in `path` (optionally buffered) fall.

    Returns the number of pixels newly constrained. No-op if the file is absent
    or has no features in the study area.
    """
    from rasterio.features import rasterize
    from shapely.geometry import box as shp_box

    if path is None or not Path(path).exists():
        return 0
    gdf = gpd.read_file(path)
    if gdf.empty:
        return 0
    if str(gdf.crs) != working_crs:
        gdf = gdf.to_crs(working_crs)
    gdf = gdf[gdf.intersects(shp_box(*study_bbox))]
    if geom_filter is not None:
        gdf = geom_filter(gdf)
    if gdf.empty:
        return 0

    geoms = gdf.geometry
    if buffer_m:
        geoms = geoms.buffer(buffer_m)
    geoms = [g for g in geoms.values if g is not None and not g.is_empty]
    if not geoms:
        return 0

    burned = rasterize(
        [(g, 1) for g in geoms],
        out_shape=shape,
        transform=transform,
        fill=0,
        default_value=1,
        dtype="uint8",
    )
    constrained = (burned == 1) & (buildable == 1)
    n = int(constrained.sum())
    buildable[constrained] = 0
    return n


_TYPE_COLS = ("type", "land_type", "landcover", "lc_type", "desc",
              "feature_type", "feat_desc", "feat_code", "cover", "class")


def _polys_matching(gdf: gpd.GeoDataFrame, keywords: tuple[str, ...]) -> gpd.GeoDataFrame:
    """Filter a land-cover GeoDataFrame to polygons whose type matches keywords."""
    for col in gdf.columns:
        if col.lower() in _TYPE_COLS:
            mask = gdf[col].astype(str).str.lower().str.contains("|".join(keywords), na=False)
            if mask.any():
                return gdf[mask]
    return gdf.iloc[0:0]


def _water_polys(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Water/wetland polygons (not buildable / not open ground)."""
    return _polys_matching(gdf, ("water", "lake", "pond", "wetland", "bog", "marsh", "swamp"))


def _forest_polys(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Treed/forest polygons. In NS the NSTDB land-cover layer is mostly
    'TREE AREA' — forest is the dominant constraint on cleared open ground for
    ground-mount solar and building pads, so it is excluded from buildable."""
    return _polys_matching(gdf, ("tree", "forest", "wood", "treed"))


def build_buildability_mask(
    slope_path: Path | None,
    land_cover_path: Path | None,
    water_path: Path | None,
    buildings_path: Path | None,
    study_bbox: tuple[float, float, float, float],
    working_crs: str,
    slope_threshold_deg: float = 20.0,
    water_buffer_m: float = 15.0,
    building_buffer_m: float = 10.0,
    dem_path: Path | None = None,
    sea_level_m: float | None = None,
    waterbodies_path: Path | None = None,
) -> Path | None:
    """Build a raster mask where 1 = buildable (open ground), 0 = constrained.

    Constraints applied: steep slope (and slope-nodata), at/below-sea-level land
    (ocean/coast proxy from the DEM), watercourses + waterbody polygons
    (buffered), forest/treed area, and existing buildings (buffered). This is
    the fix for the prior version, which applied only the slope rule and so
    marked ~all land — including water and sea-level flats — as buildable.
    Returns the mask path, or None if no slope raster is available.
    """
    if slope_path is None or not slope_path.exists():
        logger.warning("No slope raster available; cannot build buildability mask")
        return None

    with rasterio.open(slope_path) as src:
        slope = src.read(1)
        meta = src.meta.copy()
        transform = src.transform
        nodata = src.nodata
    shape = slope.shape

    # Slope constraint — gentle enough AND has valid slope data
    buildable = slope <= slope_threshold_deg
    if nodata is not None:
        buildable &= slope != nodata
    buildable = buildable.astype(np.uint8)
    n_slope_ok = int(buildable.sum())

    # Sea-level / ocean exclusion from the DEM (cheap coastline proxy)
    n_sea = 0
    if dem_path is not None and Path(dem_path).exists() and sea_level_m is not None:
        with rasterio.open(dem_path) as dsrc:
            dem = dsrc.read(1)
            dnd = dsrc.nodata
        if dem.shape == shape:
            sea = dem <= sea_level_m
            if dnd is not None:
                sea &= dem != dnd
            sea &= buildable == 1
            n_sea = int(sea.sum())
            buildable[sea] = 0
        else:
            logger.warning("DEM shape %s != slope shape %s; skipping sea-level mask", dem.shape, shape)

    # Watercourses + waterbody polygons → not buildable
    n_water = _burn_constraint(
        buildable, water_path, shape, transform, working_crs, study_bbox,
        buffer_m=water_buffer_m,
    )
    n_water += _burn_constraint(
        buildable, waterbodies_path, shape, transform, working_crs, study_bbox,
        buffer_m=water_buffer_m,
    )
    n_water += _burn_constraint(
        buildable, land_cover_path, shape, transform, working_crs, study_bbox,
        buffer_m=water_buffer_m, geom_filter=_water_polys,
    )
    # Forest / treed area → not open ground (clearing is costly; excludes most
    # of rural NS, which is what makes open_ground a real differentiator)
    n_forest = _burn_constraint(
        buildable, land_cover_path, shape, transform, working_crs, study_bbox,
        geom_filter=_forest_polys,
    )
    # Existing buildings (with a small setback) → not buildable
    n_bldg = _burn_constraint(
        buildable, buildings_path, shape, transform, working_crs, study_bbox,
        buffer_m=building_buffer_m,
    )

    mask_path = slope_path.parent / "buildability_mask.tif"
    meta.update(dtype="uint8", nodata=255, compress="lzw")
    with rasterio.open(mask_path, "w", **meta) as dst:
        dst.write(buildable, 1)

    frac = buildable.mean() * 100
    logger.info(
        "Built buildability mask: %s (slope-ok=%d, -sea=%d, -water=%d, -forest=%d, "
        "-buildings=%d; %.1f%% open/buildable)",
        mask_path, n_slope_ok, n_sea, n_water, n_forest, n_bldg, frac,
    )
    return mask_path

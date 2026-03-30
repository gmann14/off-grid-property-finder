"""CRS and datum utilities."""

import logging
from pathlib import Path

import geopandas as gpd
import rasterio
from rasterio.warp import calculate_default_transform, reproject, Resampling

from src.constants import WORKING_CRS

logger = logging.getLogger("property_finder")


def ensure_vector_crs(gdf: gpd.GeoDataFrame, target_crs: str = WORKING_CRS) -> gpd.GeoDataFrame:
    if gdf.crs is None:
        raise ValueError("GeoDataFrame has no CRS set")
    if gdf.crs.to_epsg() != int(target_crs.split(":")[1]):
        logger.info("Reprojecting vector from %s to %s", gdf.crs, target_crs)
        return gdf.to_crs(target_crs)
    return gdf


def reproject_raster(src_path: Path, dst_path: Path, target_crs: str = WORKING_CRS) -> Path:
    with rasterio.open(src_path) as src:
        if src.crs and src.crs.to_epsg() == int(target_crs.split(":")[1]):
            logger.debug("Raster %s already in %s", src_path, target_crs)
            return src_path

        transform, width, height = calculate_default_transform(
            src.crs, target_crs, src.width, src.height, *src.bounds
        )
        kwargs = src.meta.copy()
        kwargs.update(crs=target_crs, transform=transform, width=width, height=height)

        dst_path.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(dst_path, "w", **kwargs) as dst:
            for i in range(1, src.count + 1):
                reproject(
                    source=rasterio.band(src, i),
                    destination=rasterio.band(dst, i),
                    src_transform=src.transform,
                    src_crs=src.crs,
                    dst_transform=transform,
                    dst_crs=target_crs,
                    resampling=Resampling.bilinear,
                )

    logger.info("Reprojected raster to %s: %s", target_crs, dst_path)
    return dst_path

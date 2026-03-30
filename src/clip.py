"""Study-area clipping for rasters and vectors."""

import logging
from pathlib import Path

import geopandas as gpd
import rasterio
from rasterio.mask import mask as rasterio_mask
from shapely.geometry import box

logger = logging.getLogger("property_finder")


def clip_vector(
    src_path: Path,
    dst_path: Path,
    bbox: tuple[float, float, float, float],
    target_crs: str | None = None,
) -> Path:
    gdf = gpd.read_file(src_path)
    if target_crs and gdf.crs and str(gdf.crs) != target_crs:
        gdf = gdf.to_crs(target_crs)

    bbox_geom = box(*bbox)
    clipped = gdf[gdf.intersects(bbox_geom)].copy()
    clipped = gpd.clip(clipped, bbox_geom)

    dst_path.parent.mkdir(parents=True, exist_ok=True)
    clipped.to_file(dst_path, driver="GPKG")
    logger.info("Clipped vector to study area: %s (%d features)", dst_path, len(clipped))
    return dst_path


def clip_raster(
    src_path: Path,
    dst_path: Path,
    bbox: tuple[float, float, float, float],
) -> Path:
    bbox_geom = box(*bbox)

    with rasterio.open(src_path) as src:
        out_image, out_transform = rasterio_mask(src, [bbox_geom], crop=True)
        out_meta = src.meta.copy()
        out_meta.update(
            height=out_image.shape[1],
            width=out_image.shape[2],
            transform=out_transform,
        )

    dst_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(dst_path, "w", **out_meta) as dst:
        dst.write(out_image)

    logger.info("Clipped raster to study area: %s", dst_path)
    return dst_path

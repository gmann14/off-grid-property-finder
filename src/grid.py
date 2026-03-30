"""Candidate grid generation and rural-eligibility filtering."""

import logging

import geopandas as gpd
import numpy as np
from shapely.geometry import box

from src.constants import DEFAULT_CELL_SIZE_M

logger = logging.getLogger("property_finder")


def generate_candidate_grid(
    bbox: tuple[float, float, float, float],
    cell_size: float = DEFAULT_CELL_SIZE_M,
    crs: str = "EPSG:2961",
) -> gpd.GeoDataFrame:
    """Generate a regular grid of square cells covering the study area.

    Each cell is a Shapely Polygon. The grid is aligned to the bbox origin.
    Returns a GeoDataFrame with integer cell_id index and geometry column.
    """
    xmin, ymin, xmax, ymax = bbox
    cols = np.arange(xmin, xmax, cell_size)
    rows = np.arange(ymin, ymax, cell_size)

    cells = []
    cell_ids = []
    cell_id = 0
    for x in cols:
        for y in rows:
            cells.append(box(x, y, x + cell_size, y + cell_size))
            cell_ids.append(cell_id)
            cell_id += 1

    gdf = gpd.GeoDataFrame({"cell_id": cell_ids}, geometry=cells, crs=crs)
    gdf = gdf.set_index("cell_id")
    logger.info("Generated candidate grid: %d cells (%d cols x %d rows)", len(gdf), len(cols), len(rows))
    return gdf


def filter_by_rural_mask(
    grid: gpd.GeoDataFrame,
    rural_mask: gpd.GeoDataFrame | None,
) -> gpd.GeoDataFrame:
    """Filter candidate cells by rural-eligibility mask using centroid containment.

    Cells whose centroid falls inside a rural-eligible polygon are kept.
    If rural_mask is None, all cells pass.
    """
    if rural_mask is None:
        logger.info("No rural mask; all %d cells pass", len(grid))
        return grid

    centroids = grid.geometry.centroid
    centroid_gdf = gpd.GeoDataFrame(
        {"cell_id": grid.index}, geometry=centroids, crs=grid.crs
    )

    joined = gpd.sjoin(centroid_gdf, rural_mask, how="inner", predicate="within")
    kept_ids = joined["cell_id"].unique()
    filtered = grid.loc[grid.index.isin(kept_ids)]

    logger.info(
        "Rural mask filter: %d of %d cells pass",
        len(filtered), len(grid),
    )
    return filtered

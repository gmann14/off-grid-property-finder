"""Data preparation pipeline orchestrator.

Runs in two phases:
1. Ingest: convert raw data (GDB, OSM PBF, various CRS) into standardized GPKG/GeoTIFF
2. Derive: generate DEM derivatives, masks, and candidate grid
"""

import logging
from pathlib import Path

from src.config import Config
from src.dem import (
    generate_aspect,
    generate_exposure,
    generate_flow_accumulation,
    generate_slope,
)
from src.grid import filter_by_rural_mask, generate_candidate_grid
from src.ingest import run_ingest
from src.mask import build_buildability_mask, build_rural_mask

logger = logging.getLogger("property_finder")


def run_prepare(config: Config, logger: logging.Logger) -> None:
    """Run the full data preparation pipeline."""
    processed = config.paths.processed
    processed.mkdir(parents=True, exist_ok=True)
    bbox = config.study_area.bbox

    # Phase 1: Ingest raw data into standardized formats
    logger.info("=== Phase 1: Data ingestion ===")
    ingested = run_ingest(config, logger)

    # Phase 2: Generate DEM derivatives
    logger.info("=== Phase 2: DEM derivatives ===")
    dem_path = ingested.get("dem")
    if dem_path and dem_path.exists():
        from src.constants import FLOW_ACCUM_TARGET_RES_M
        generate_slope(dem_path, processed / "slope.tif")
        generate_aspect(dem_path, processed / "aspect.tif")
        generate_exposure(dem_path, processed / "exposure.tif")
        generate_flow_accumulation(
            dem_path, processed / "flow_accumulation.tif",
            target_res_m=FLOW_ACCUM_TARGET_RES_M,
        )
    else:
        logger.warning("No DEM available; skipping derivative generation")

    # Phase 3: Build masks
    logger.info("=== Phase 3: Mask generation ===")
    land_cover_path = ingested.get("land_cover")
    buildings_path = ingested.get("buildings")
    slope_path = processed / "slope.tif"

    rural_mask = build_rural_mask(
        land_cover_path=land_cover_path,
        buildings_path=buildings_path,
        study_bbox=bbox,
        working_crs=config.working_crs,
    )

    streams_path = processed / "streams.gpkg"
    dem_path_p = processed / "dem.tif"
    waterbodies_path = processed / "waterbodies.gpkg"
    from src.constants import SEA_LEVEL_M
    build_buildability_mask(
        slope_path=slope_path if slope_path.exists() else None,
        land_cover_path=land_cover_path,
        water_path=streams_path if streams_path.exists() else None,
        buildings_path=buildings_path,
        study_bbox=bbox,
        working_crs=config.working_crs,
        dem_path=dem_path_p if dem_path_p.exists() else None,
        sea_level_m=SEA_LEVEL_M,
        waterbodies_path=waterbodies_path if waterbodies_path.exists() else None,
    )

    # Phase 4: Generate candidate grid
    logger.info("=== Phase 4: Candidate grid generation ===")
    grid = generate_candidate_grid(
        bbox=bbox,
        cell_size=config.cell_size_m,
        crs=config.working_crs,
    )

    # Filter by rural mask
    grid = filter_by_rural_mask(grid, rural_mask)

    # Save grid
    grid_path = processed / "candidate_grid.gpkg"
    grid.to_file(grid_path, driver="GPKG")
    logger.info("Saved candidate grid: %s (%d cells)", grid_path, len(grid))

    logger.info("=== Data preparation complete ===")

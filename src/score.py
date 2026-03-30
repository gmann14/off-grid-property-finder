"""Scoring engine orchestrator."""

import logging
from pathlib import Path

import geopandas as gpd

from src.config import Config
from src.exclusions import apply_exclusions, load_exclusions
from src.export import export_results

# Import scorers to trigger registration
import src.scoring.elevation  # noqa: F401
import src.scoring.solar  # noqa: F401
import src.scoring.access  # noqa: F401
import src.scoring.hydro  # noqa: F401
import src.scoring.buildable  # noqa: F401

from src.scoring.confidence import compute_confidence
from src.scoring.preferences import aggregate_to_parcels
from src.scoring.registry import compute_composite_score

logger = logging.getLogger("property_finder")


def run_score(config: Config, logger: logging.Logger) -> None:
    """Run the full scoring pipeline."""
    processed = config.paths.processed
    output = config.paths.output
    output.mkdir(parents=True, exist_ok=True)

    # Load candidate grid
    grid_path = processed / "candidate_grid.gpkg"
    if not grid_path.exists():
        logger.error("Candidate grid not found at %s. Run 'prepare' first.", grid_path)
        return

    candidates = gpd.read_file(grid_path)
    logger.info("Loaded %d candidate cells", len(candidates))

    # Step 1: Apply exclusions
    logger.info("=== Step 1: Applying exclusions ===")
    protected_path = processed / "protected_areas.gpkg"
    flood_path = processed / "flood.gpkg"

    exclusions = load_exclusions(
        protected_path=protected_path if protected_path.exists() else None,
        flood_path=flood_path if flood_path.exists() else None,
        working_crs=config.working_crs,
        study_bbox=config.study_area.bbox,
    )

    candidates = apply_exclusions(
        candidates, exclusions,
        overlap_threshold=config.exclusion_overlap_threshold,
    )

    # Step 2: Score eligible cells
    logger.info("=== Step 2: Scoring cells ===")
    candidates = compute_composite_score(candidates, config)

    # Step 3: Compute confidence
    logger.info("=== Step 3: Computing confidence ===")
    data_flags = _detect_data_flags(config)
    candidates = compute_confidence(candidates, config, data_flags)

    # Step 4: Rank
    logger.info("=== Step 4: Ranking ===")
    eligible = candidates[candidates.get("status") != "excluded"].copy()
    eligible["rank"] = eligible["score"].rank(ascending=False, method="min").astype("Int64")
    candidates = candidates.join(eligible[["rank"]])

    # Save scored cells
    scored_path = output / "scored_cells.gpkg"
    candidates.to_file(scored_path, driver="GPKG")
    logger.info("Saved scored cells: %s", scored_path)

    # Step 5: Parcel aggregation (if parcels available)
    parcels_path = processed / "parcels.gpkg"
    if parcels_path.exists():
        logger.info("=== Step 5: Parcel aggregation ===")
        parcels = gpd.read_file(parcels_path)
        parcels = aggregate_to_parcels(candidates, parcels)
        parcels_out = output / "scored_parcels.gpkg"
        parcels.to_file(parcels_out, driver="GPKG")
        logger.info("Saved scored parcels: %s", parcels_out)
    else:
        logger.info("No parcel data; skipping Stage B aggregation")

    # Step 6: Export
    logger.info("=== Step 6: Exporting results ===")
    export_results(candidates, output)

    logger.info("=== Scoring complete ===")


def _detect_data_flags(config: Config) -> dict[str, bool]:
    """Detect which data quality flags apply based on available files."""
    processed = config.paths.processed
    flags = {}

    flood_path = processed / "flood.gpkg"
    flags["no_flood_data"] = not flood_path.exists()

    flow_acc_path = processed / "flow_accumulation.tif"
    flags["hydro_drainage_proxy_only"] = not flow_acc_path.exists()

    # Check DEM resolution
    dem_path = processed / "dem.tif"
    if dem_path.exists():
        try:
            import rasterio
            with rasterio.open(dem_path) as src:
                res = src.res[0]
                flags["hydro_20m_dem"] = res >= 20
        except Exception:
            flags["hydro_20m_dem"] = False
    else:
        flags["hydro_20m_dem"] = False

    return flags

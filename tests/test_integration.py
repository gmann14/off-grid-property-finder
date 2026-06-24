"""Integration tests using synthetic fixtures."""

import geopandas as gpd
import pandas as pd
import pytest

from src.exclusions import apply_exclusions, load_exclusions
from src.grid import generate_candidate_grid
from src.scoring.confidence import compute_confidence
from src.scoring.registry import _REGISTRY, compute_composite_score, register

CRS = "EPSG:2961"


def test_full_pipeline_synthetic(small_grid, config_with_paths, sample_exclusion_zones):
    """End-to-end: grid -> exclusions -> score -> confidence -> rank."""
    grid = small_grid

    # Apply exclusions
    exclusions = load_exclusions(
        sample_exclusion_zones, None, CRS, config_with_paths.study_area.bbox
    )
    grid = apply_exclusions(grid, exclusions)

    # Verify some cells are excluded
    assert (grid["status"] == "excluded").any()
    assert (grid["status"] == "eligible").any()

    # Register test scorers (use simple deterministic values)
    @register("_int_hydro")
    def _h(c, cfg):
        return pd.Series(60.0, index=c.index)

    @register("_int_solar")
    def _s(c, cfg):
        return pd.Series(40.0, index=c.index)

    config_with_paths.weights = {"_int_hydro": 60, "_int_solar": 40}
    config_with_paths.enabled_criteria = ["_int_hydro", "_int_solar"]

    # Score
    grid = compute_composite_score(grid, config_with_paths)

    # Eligible cells should have scores
    eligible = grid[grid["status"] == "eligible"]
    assert eligible["score"].notna().all()
    # Expected: 60*0.6 + 40*0.4 = 52
    assert eligible["score"].iloc[0] == pytest.approx(52.0, abs=0.1)

    # Excluded cells should have null scores
    excluded = grid[grid["status"] == "excluded"]
    assert excluded["score"].isna().all()

    # Confidence
    grid = compute_confidence(grid, config_with_paths, data_flags={"no_flood_data": True})
    eligible_conf = grid[grid["status"] == "eligible"]
    assert eligible_conf["confidence"].notna().all()
    assert (eligible_conf["confidence"] == 80.0).all()  # 100 - 20

    # Cleanup
    del _REGISTRY["_int_hydro"]
    del _REGISTRY["_int_solar"]


def test_run_score_end_to_end_default_criteria(
    tmp_path, small_grid, sample_dem, sample_slope, sample_aspect,
    sample_roads, sample_streams, sample_civic, sample_parcels, sample_exclusion_zones
):
    """Full run_score with the DEFAULT criteria (hydro/access/open_ground/wind/
    elevation) + exclusions + Stage-B parcels/typing on synthetic data.

    Fast (seconds) smoke that exercises the real orchestrator end to end — this
    is the guard that catches crashes like None-into-bool-column before a
    15-minute full run. Asserts it completes and emits the expected outputs.
    """
    import shutil
    import geopandas as gpd
    from shapely.geometry import box
    from src.config import Config, Paths, StudyArea
    from src.mask import build_buildability_mask
    from src.dem import generate_exposure
    from src.score import run_score

    BBOX = (380000.0, 4900000.0, 381000.0, 4901000.0)
    processed = tmp_path / "proc"
    processed.mkdir()
    output = tmp_path / "out"

    # Lay out the processed inputs the scorers expect
    for src_path, name in [
        (sample_dem, "dem.tif"), (sample_slope, "slope.tif"), (sample_aspect, "aspect.tif"),
        (sample_roads, "roads.gpkg"), (sample_streams, "streams.gpkg"),
        (sample_civic, "civic.gpkg"), (sample_parcels, "parcels.gpkg"),
        (sample_exclusion_zones, "protected_areas.gpkg"),
    ]:
        shutil.copy2(src_path, processed / name)
    small_grid.to_file(processed / "candidate_grid.gpkg", driver="GPKG")
    build_buildability_mask(processed / "slope.tif", None, None, None, BBOX, CRS)
    generate_exposure(processed / "dem.tif", processed / "exposure.tif")
    # A couple of building footprints for parcel typing
    gpd.GeoDataFrame(
        geometry=[box(380100, 4900100, 380120, 4900120),
                  box(380600, 4900600, 380620, 4900620)],
        crs=CRS,
    ).to_file(processed / "buildings.gpkg", driver="GPKG")

    cfg = Config(  # default weights + enabled_criteria
        study_area=StudyArea(bbox=BBOX, name="smoke"),
        paths=Paths(raw=tmp_path / "raw", processed=processed, output=output),
    )

    import logging
    run_score(cfg, logging.getLogger("test"))  # must not raise

    # Cell outputs
    assert (output / "scored_cells.csv").exists()
    assert (output / "ranked_eligible.csv").exists()
    cells = pd.read_csv(output / "scored_cells.csv")
    for col in ["score", "score_allrounder", "score_hydro", "score_access",
                "score_open_ground", "score_wind", "score_elevation", "wind_worth_it"]:
        assert col in cells.columns, f"missing {col}"

    # Stage-B parcel outputs
    assert (output / "scored_parcels.gpkg").exists()
    assert (output / "ranked_parcels.csv").exists()
    parcels = gpd.read_file(output / "scored_parcels.gpkg")
    for col in ["PID", "parcel_type", "n_buildings", "severance_candidate", "rank"]:
        assert col in parcels.columns, f"missing parcel col {col}"


def test_grid_exclusion_count(small_grid, sample_exclusion_zones):
    """Verify exclusion zone correctly excludes cells in the NE quadrant."""
    exclusions = load_exclusions(
        sample_exclusion_zones, None, CRS,
        (380000.0, 4900000.0, 381000.0, 4901000.0),
    )
    result = apply_exclusions(small_grid, exclusions)
    excluded = result[result["status"] == "excluded"]
    eligible = result[result["status"] == "eligible"]

    # Exclusion zone covers NE quadrant (500m x 500m = 4 cells of 250m)
    # Cells with centroid in the exclusion zone should be excluded
    assert len(excluded) > 0
    assert len(eligible) > 0
    assert len(excluded) + len(eligible) == 16

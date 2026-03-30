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

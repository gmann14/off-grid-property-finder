"""Tests for confidence scoring."""

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

from src.config import Config, StudyArea
from src.scoring.confidence import compute_confidence


def _make_candidates(**kwargs):
    defaults = {
        "status": "eligible",
        "score": 70.0,
        "score_access": 60.0,
        "score_hydro": 40.0,
    }
    defaults.update(kwargs)
    return gpd.GeoDataFrame(
        {k: [v] for k, v in defaults.items()},
        geometry=[box(0, 0, 250, 250)],
        crs="EPSG:2961",
    )


def _make_config():
    return Config(study_area=StudyArea(bbox=(0, 0, 1000, 1000)))


def test_full_confidence_no_flags():
    candidates = _make_candidates()
    result = compute_confidence(candidates, _make_config(), data_flags={})
    assert result["confidence"].iloc[0] == 100.0
    assert result["confidence_band"].iloc[0] == "high"


def test_flood_deduction():
    candidates = _make_candidates()
    result = compute_confidence(
        candidates, _make_config(),
        data_flags={"no_flood_data": True},
    )
    assert result["confidence"].iloc[0] == 80.0
    assert result["confidence_band"].iloc[0] == "high"


def test_multiple_global_deductions():
    candidates = _make_candidates()
    result = compute_confidence(
        candidates, _make_config(),
        data_flags={"no_flood_data": True, "hydro_drainage_proxy_only": True},
    )
    # 100 - 20 (flood) - 20 (drainage) = 60, no per-cell flags
    # (default candidates have score_access=60 >= 50, score_hydro=40 > 0)
    assert result["confidence"].iloc[0] == 60.0
    assert result["confidence_band"].iloc[0] == "medium"


def test_confidence_clamp_to_zero():
    candidates = _make_candidates()
    result = compute_confidence(
        candidates, _make_config(),
        data_flags={
            "no_flood_data": True,
            "hydro_drainage_proxy_only": True,
            "hydro_20m_dem": True,
            "incomplete_land_cover_mask": True,
            "no_road_evidence_200m": True,
        },
    )
    assert result["confidence"].iloc[0] >= 0


def test_excluded_cells_null_confidence():
    candidates = _make_candidates(status="excluded")
    result = compute_confidence(candidates, _make_config(), data_flags={})
    assert pd.isna(result["confidence"].iloc[0])


def test_access_flag():
    candidates = _make_candidates(score_access=30)
    result = compute_confidence(candidates, _make_config(), data_flags={})
    assert "access_unverified" in result["flags"].iloc[0]


def test_per_cell_access_deduction():
    """Access-unverified flag should deduct 15 from confidence."""
    candidates = _make_candidates(score_access=30)
    result = compute_confidence(candidates, _make_config(), data_flags={})
    assert result["confidence"].iloc[0] == 85.0


def test_per_cell_hydro_deduction():
    """Hydro low-confidence flag should deduct 10 from confidence."""
    candidates = _make_candidates(score_hydro=0)
    result = compute_confidence(candidates, _make_config(), data_flags={})
    assert result["confidence"].iloc[0] == 90.0
    assert "hydro_low_confidence" in result["flags"].iloc[0]


def test_per_cell_both_flags():
    """Both per-cell flags should stack deductions."""
    candidates = _make_candidates(score_access=20, score_hydro=0)
    result = compute_confidence(candidates, _make_config(), data_flags={})
    # 100 - 15 (access) - 10 (hydro) = 75
    assert result["confidence"].iloc[0] == 75.0
    assert result["confidence_band"].iloc[0] == "medium"


def test_per_cell_flags_plus_global():
    """Per-cell and global deductions should stack."""
    candidates = _make_candidates(score_access=20, score_hydro=0)
    result = compute_confidence(
        candidates, _make_config(),
        data_flags={"no_flood_data": True},
    )
    # 100 - 20 (global) - 15 (access) - 10 (hydro) = 55
    assert result["confidence"].iloc[0] == 55.0
    assert result["confidence_band"].iloc[0] == "medium"


def test_mixed_cells_different_confidence():
    """Different cells should get different confidence based on their flags."""
    gdf = gpd.GeoDataFrame(
        {
            "status": ["eligible", "eligible", "eligible"],
            "score": [80.0, 60.0, 40.0],
            "score_access": [100.0, 30.0, 0.0],
            "score_hydro": [60.0, 0.0, 0.0],
        },
        geometry=[box(0, 0, 250, 250), box(250, 0, 500, 250), box(500, 0, 750, 250)],
        crs="EPSG:2961",
    )
    result = compute_confidence(gdf, _make_config(), data_flags={})
    # Cell 0: no flags → 100
    assert result["confidence"].iloc[0] == 100.0
    # Cell 1: access_unverified + hydro_low → 100 - 15 - 10 = 75
    assert result["confidence"].iloc[1] == 75.0
    # Cell 2: access_unverified + hydro_low → 100 - 15 - 10 = 75
    assert result["confidence"].iloc[2] == 75.0
    # Verify different bands emerge
    assert result["confidence"].iloc[0] != result["confidence"].iloc[1]

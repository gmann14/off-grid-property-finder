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


def test_multiple_deductions():
    candidates = _make_candidates()
    result = compute_confidence(
        candidates, _make_config(),
        data_flags={"no_flood_data": True, "hydro_drainage_proxy_only": True},
    )
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

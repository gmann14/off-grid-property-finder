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


def test_per_cell_access_deduction_reads_config():
    """Regression: the per-cell access amount was hard-coded at 15, so
    ``confidence_deductions.no_road_evidence_200m`` in config.yaml did nothing."""
    cfg = _make_config()
    cfg.confidence_deductions = {**cfg.confidence_deductions, "no_road_evidence_200m": 25}
    result = compute_confidence(_make_candidates(score_access=30), cfg, data_flags={})
    assert result["confidence"].iloc[0] == 75.0


def test_per_cell_access_deduction_falls_back_to_default_when_key_absent():
    cfg = _make_config()
    cfg.confidence_deductions = {"no_flood_data": 20}  # no no_road_evidence_200m key
    result = compute_confidence(_make_candidates(score_access=30), cfg, data_flags={})
    assert result["confidence"].iloc[0] == 85.0


def test_global_deduction_falls_back_to_default_when_key_absent():
    cfg = _make_config()
    cfg.confidence_deductions = {}
    result = compute_confidence(_make_candidates(), cfg,
                                data_flags={"incomplete_land_cover_mask": True})
    assert result["confidence"].iloc[0] == 90.0


def test_hydro_zero_is_not_low_confidence():
    """Regression: hydro_low_confidence (with an unlisted -10) went on every
    cell scoring hydro 0, 81% of the real eligible cells. A cell with no
    nearby stream is a low hydro score, not a low-confidence estimate."""
    candidates = _make_candidates(score_hydro=0)
    result = compute_confidence(candidates, _make_config(), data_flags={})
    assert result["confidence"].iloc[0] == 100.0
    assert result["flags"].iloc[0] == ""


@pytest.mark.parametrize("data_flag", ["hydro_drainage_proxy_only", "hydro_20m_dem"])
def test_hydro_low_confidence_marks_proxy_or_coarse_dem_estimates(data_flag):
    """SPEC §2.1: hydro estimate based on a low-resolution DEM or the
    drainage proxy only. Only the global deduction applies, once."""
    candidates = _make_candidates(score_hydro=60)
    result = compute_confidence(candidates, _make_config(), data_flags={data_flag: True})
    assert result["flags"].iloc[0] == "hydro_low_confidence"
    assert result["confidence"].iloc[0] == 100.0 - _make_config().confidence_deductions[data_flag]


def test_coastal_low_elevation_only_without_flood_data():
    """SPEC §2.1: low elevation near the coast with no flood layer. 70/40/10/0
    are the under-20 m (low-coastal) elevation bands; 50 is the no-DEM score."""
    gdf = gpd.GeoDataFrame(
        {"status": ["eligible"] * 5, "score": [50.0] * 5,
         "score_elevation": [100.0, 70.0, 10.0, 0.0, 50.0]},
        geometry=[box(250 * i, 0, 250 * (i + 1), 250) for i in range(5)],
        crs="EPSG:2961",
    )
    result = compute_confidence(gdf, _make_config(), data_flags={"no_flood_data": True})
    assert result["flags"].tolist() == [
        "", "coastal_low_elevation", "coastal_low_elevation", "coastal_low_elevation", ""]
    assert (result["confidence"] == 80.0).all()  # the global -20 only
    with_flood = compute_confidence(gdf, _make_config(), data_flags={"no_flood_data": False})
    assert (with_flood["flags"] == "").all()


def test_solar_low_confidence_when_mask_lacks_land_cover():
    candidates = _make_candidates(score_open_ground=65.0)
    result = compute_confidence(candidates, _make_config(),
                                data_flags={"incomplete_land_cover_mask": True})
    assert result["flags"].iloc[0] == "solar_low_confidence"
    assert result["confidence"].iloc[0] == 90.0
    # No open-area criterion scored: nothing to flag (the global -10 still applies).
    result = compute_confidence(_make_candidates(), _make_config(),
                                data_flags={"incomplete_land_cover_mask": True})
    assert result["flags"].iloc[0] == ""


def test_wind_exposure_proxy_only_when_wind_is_from_terrain():
    candidates = _make_candidates(score_wind=80.0)
    result = compute_confidence(candidates, _make_config(), data_flags={"wind_proxy_only": True})
    assert result["flags"].iloc[0] == "wind_exposure_proxy_only"
    assert result["confidence"].iloc[0] == 90.0
    result = compute_confidence(candidates, _make_config(), data_flags={"wind_proxy_only": False})
    assert result["flags"].iloc[0] == ""


def test_all_flags_in_spec_order_and_excluded_cells_get_none():
    gdf = gpd.GeoDataFrame(
        {"status": ["eligible", "excluded"], "score": [40.0, None],
         "score_access": [20.0, None], "score_elevation": [40.0, None],
         "score_hydro": [0.0, None], "score_open_ground": [0.0, None],
         "score_wind": [15.0, None]},
        geometry=[box(0, 0, 250, 250), box(250, 0, 500, 250)],
        crs="EPSG:2961",
    )
    all_gaps = {"no_flood_data": True, "hydro_drainage_proxy_only": True,
                "incomplete_land_cover_mask": True, "wind_proxy_only": True}
    result = compute_confidence(gdf, _make_config(), data_flags=all_gaps)
    assert result["flags"].iloc[0] == (
        "access_unverified; coastal_low_elevation; hydro_low_confidence; "
        "solar_low_confidence; wind_exposure_proxy_only")
    assert result["flags"].iloc[1] == ""
    # 100 - 20 - 20 - 10 - 10 (global) - 15 (access) = 25
    assert result["confidence"].iloc[0] == 25.0


def test_per_cell_flags_plus_global():
    """Per-cell and global deductions should stack."""
    candidates = _make_candidates(score_access=20, score_hydro=0)
    result = compute_confidence(
        candidates, _make_config(),
        data_flags={"no_flood_data": True},
    )
    # 100 - 20 (global) - 15 (access) = 65
    assert result["confidence"].iloc[0] == 65.0
    assert result["confidence_band"].iloc[0] == "medium"


def test_no_score_columns_present_no_crash():
    """If score_access/score_hydro aren't present at all (e.g. those criteria
    are disabled in config), confidence must not crash and no per-cell flags
    should fire — matches the old cell.get(...) -> None -> notna() -> False path."""
    gdf = gpd.GeoDataFrame(
        {"status": ["eligible"], "score": [70.0]},
        geometry=[box(0, 0, 250, 250)],
        crs="EPSG:2961",
    )
    result = compute_confidence(gdf, _make_config(), data_flags={})
    assert result["flags"].iloc[0] == ""
    assert result["confidence"].iloc[0] == 100.0


def test_no_status_column_counts_every_cell_as_eligible():
    """Regression: with no ``status`` column, ``candidates.get("status") ==
    "excluded"`` was a plain False and ``False.any()`` raised AttributeError."""
    gdf = gpd.GeoDataFrame({"score": [70.0]}, geometry=[box(0, 0, 250, 250)], crs="EPSG:2961")
    result = compute_confidence(gdf, _make_config(), data_flags={})
    assert result["confidence"].iloc[0] == 100.0
    assert result["confidence_band"].iloc[0] == "high"


def test_flags_string_content_single_and_combined():
    """The joined flags string: single flag as-is, two joined with '; ' in
    SPEC order, and no flag when neither condition applies."""
    gdf = gpd.GeoDataFrame(
        {
            "status": ["eligible"] * 3,
            "score": [80.0, 60.0, 40.0],
            "score_access": [100.0, 20.0, 100.0],   # none / unverified / none
            "score_hydro": [40.0, 0.0, 0.0],
        },
        geometry=[box(0, 0, 250, 250), box(250, 0, 500, 250), box(500, 0, 750, 250)],
        crs="EPSG:2961",
    )
    result = compute_confidence(gdf, _make_config(), data_flags={"hydro_20m_dem": True})
    assert result["flags"].iloc[0] == "hydro_low_confidence"
    assert result["flags"].iloc[1] == "access_unverified; hydro_low_confidence"
    assert result["flags"].iloc[2] == "hydro_low_confidence"


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
    # Cells 1 and 2: access_unverified → 100 - 15 = 85 (hydro 0 is no flag)
    assert result["confidence"].iloc[1] == 85.0
    assert result["confidence"].iloc[2] == 85.0
    # Verify different bands emerge
    assert result["confidence"].iloc[0] != result["confidence"].iloc[1]

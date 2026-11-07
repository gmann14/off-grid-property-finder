"""Tests for preference scoring and parcel aggregation."""

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

from src.scoring.preferences import (
    aggregate_to_parcels,
    classify_parcels,
    score_parcel_size,
)


def test_score_parcel_size():
    parcels = gpd.GeoDataFrame(
        {"area_acres": [120, 60, 30, 15, 7, 3, 1.5]},
        geometry=[box(i, 0, i + 1, 1) for i in range(7)],
        crs="EPSG:2961",
    )
    scores = score_parcel_size(parcels)
    assert scores.iloc[0] == 100  # 120 acres (100+ tier)
    assert scores.iloc[1] == 92   # 60 acres
    assert scores.iloc[2] == 80   # 30 acres
    assert scores.iloc[3] == 60   # 15 acres
    assert scores.iloc[4] == 40   # 7 acres
    assert scores.iloc[5] == 20   # 3 acres
    assert scores.iloc[6] == 5    # 1.5 acres


def test_aggregate_to_parcels():
    # Two cells, one parcel
    candidates = gpd.GeoDataFrame(
        {"score": [80, 60], "status": ["eligible", "eligible"]},
        geometry=[box(0, 0, 250, 250), box(250, 0, 500, 250)],
        crs="EPSG:2961",
    )
    parcels = gpd.GeoDataFrame(
        {"parcel_id": [1]},
        geometry=[box(0, 0, 500, 250)],
        crs="EPSG:2961",
    )

    result = aggregate_to_parcels(candidates, parcels, top_n=2)
    assert pd.notna(result["score"].iloc[0])
    assert result["cell_score"].iloc[0] == pytest.approx(70.0)  # mean of [80, 60]


def test_aggregate_no_cells_in_parcel():
    candidates = gpd.GeoDataFrame(
        {"score": [80], "status": ["eligible"]},
        geometry=[box(0, 0, 250, 250)],
        crs="EPSG:2961",
    )
    parcels = gpd.GeoDataFrame(
        {"parcel_id": [1]},
        geometry=[box(1000, 1000, 1500, 1500)],  # far away
        crs="EPSG:2961",
    )

    result = aggregate_to_parcels(candidates, parcels)
    assert pd.isna(result["score"].iloc[0])
    assert result["n_cells"].iloc[0] == 0
    assert result["flags"].iloc[0] == "parcel_no_assigned_candidates"


def test_aggregate_carries_pid_and_ranks():
    # Parcel A (high-scoring cell) should outrank parcel B (low-scoring cell)
    candidates = gpd.GeoDataFrame(
        {"score": [90, 30], "status": ["eligible", "eligible"]},
        geometry=[box(0, 0, 250, 250), box(600, 0, 850, 250)],
        crs="EPSG:2961",
    )
    parcels = gpd.GeoDataFrame(
        {"PID": ["60010001", "60010002"], "AAN": ["01", "02"]},
        geometry=[box(0, 0, 500, 250), box(500, 0, 1000, 250)],
        crs="EPSG:2961",
    )

    result = aggregate_to_parcels(candidates, parcels, top_n=3)

    # PID survives aggregation untouched
    assert list(result["PID"]) == ["60010001", "60010002"]
    assert "AAN" in result.columns
    assert result["n_cells"].tolist() == [1, 1]

    # Best parcel ranks first
    best = result.loc[result["rank"] == 1]
    assert best["PID"].iloc[0] == "60010001"
    assert result.loc[result["PID"] == "60010001", "cell_score"].iloc[0] == pytest.approx(90.0)


def test_aggregate_excludes_ineligible_cells():
    candidates = gpd.GeoDataFrame(
        {"score": [None, 50], "status": ["excluded", "eligible"]},
        geometry=[box(0, 0, 250, 250), box(250, 0, 500, 250)],
        crs="EPSG:2961",
    )
    parcels = gpd.GeoDataFrame(
        {"PID": ["60010001"]},
        geometry=[box(0, 0, 500, 250)],
        crs="EPSG:2961",
    )

    result = aggregate_to_parcels(candidates, parcels, top_n=3)
    # Only the eligible cell counts
    assert result["n_cells"].iloc[0] == 1
    assert result["cell_score"].iloc[0] == pytest.approx(50.0)


def _parcels_with_metrics():
    # Three 500m parcels (~61.8 acres each) keyed by PID, with cell_score
    return gpd.GeoDataFrame(
        {
            "PID": ["60010001", "60010002", "60010003"],
            "area_acres": [61.8, 61.8, 61.8],
            "cell_score": [90.0, 90.0, 90.0],
        },
        geometry=[box(0, 0, 500, 500), box(500, 0, 1000, 500), box(1000, 0, 1500, 500)],
        crs="EPSG:2961",
    )


def test_classify_parcels_types_by_building_count():
    parcels = _parcels_with_metrics()
    # parcel A: 0 buildings; B: 2 buildings; C: 4 buildings
    pts = [
        (600, 250), (650, 250),                        # B -> lightly_built
        (1100, 250), (1150, 250), (1200, 250), (1250, 250),  # C -> developed
    ]
    buildings = gpd.GeoDataFrame(
        geometry=[box(x, y, x + 10, y + 10) for x, y in pts], crs="EPSG:2961"
    )
    result = classify_parcels(parcels, buildings)
    types = dict(zip(result["PID"], result["parcel_type"]))
    assert types["60010001"] == "land_only"
    assert types["60010002"] == "lightly_built"
    assert types["60010003"] == "developed"
    assert result.loc[result.PID == "60010002", "n_buildings"].iloc[0] == 2


def test_classify_parcels_severance_candidate():
    # Big (>=40 acre) parcel with 1-3 buildings and strong cell_score -> severance
    parcels = _parcels_with_metrics()
    buildings = gpd.GeoDataFrame(geometry=[box(50, 50, 60, 60)], crs="EPSG:2961")  # 1 in parcel A
    result = classify_parcels(parcels, buildings)
    a = result[result.PID == "60010001"].iloc[0]
    assert a["n_buildings"] == 1
    assert a["severance_candidate"]  # 61.8 acres, 1 building, cell_score 90
    assert "severance_candidate" in a["flags"]


def test_score_parcel_size_uses_land_acres():
    """A 60-acre lot that is mostly lake scores on its 15 acres of land."""
    parcels = gpd.GeoDataFrame(
        {"area_acres": [60.0, 60.0], "land_acres": [15.0, 60.0]},
        geometry=[box(0, 0, 1, 1), box(1, 0, 2, 1)], crs="EPSG:2961",
    )
    scores = score_parcel_size(parcels)
    assert scores.iloc[0] == 60  # 15 acres
    assert scores.iloc[1] == 92  # 60 acres


def test_classify_parcels_severance_needs_40_land_acres():
    """A 61.8-acre lot with only 30 acres of land isn't a severance candidate."""
    parcels = _parcels_with_metrics()
    parcels["land_acres"] = [30.0, 61.8, 61.8]
    buildings = gpd.GeoDataFrame(
        geometry=[box(50, 50, 60, 60), box(550, 50, 560, 60)], crs="EPSG:2961"
    )  # 1 building in A and 1 in B
    result = classify_parcels(parcels, buildings).set_index("PID")
    assert not result.loc["60010001", "severance_candidate"]
    assert result.loc["60010002", "severance_candidate"]


def test_classify_parcels_no_buildings():
    parcels = _parcels_with_metrics()
    result = classify_parcels(parcels, None)
    assert (result["n_buildings"] == 0).all()
    assert (result["parcel_type"] == "land_only").all()
    assert not result["severance_candidate"].any()


def test_aggregate_uses_assignment_points_for_shore_cells(tmp_path):
    """A shore cell whose centroid is in a water lot goes to the land parcel
    it is mostly made of once assignment points are passed."""
    from src.exclusions import apply_exclusions, load_exclusions, parcel_assignment_points

    crs = "EPSG:2961"
    cells = gpd.GeoDataFrame({"score": [80.0]}, geometry=[box(0, 0, 250, 250)], crs=crs)
    lake = box(100, -500, 150, 750)  # a 20% strip through the centroid, beyond the cell
    path = tmp_path / "water.gpkg"
    gpd.GeoDataFrame(geometry=[lake], crs=crs).to_file(path, driver="GPKG")
    exclusions = load_exclusions(None, None, crs, (-1000, -1000, 1000, 1000), water_path=path)
    cells = apply_exclusions(cells, exclusions)
    assert cells["status"].iloc[0] == "eligible"
    parcels = gpd.GeoDataFrame(
        {"PID": ["water_lot", "west_land", "east_land"]},
        geometry=[lake, box(-500, -500, 100, 750), box(150, -500, 750, 750)],
        crs=crs,
    )
    by_centroid = aggregate_to_parcels(cells, parcels).set_index("PID")
    assert by_centroid.loc["water_lot", "n_cells"] == 1
    by_land = aggregate_to_parcels(
        cells, parcels, assign_points=parcel_assignment_points(cells, exclusions)).set_index("PID")
    assert by_land.loc["water_lot", "n_cells"] == 0 and pd.isna(by_land.loc["water_lot", "score"])
    assert by_land.loc[["west_land", "east_land"], "n_cells"].sum() == 1


def _three_parcels_with_cells():
    """Parcels A (2 acres of land), B (3 mapped acres, 0.8 of them land) and C
    (no cell), each holding one 250 m cell (C none)."""
    candidates = gpd.GeoDataFrame(
        {"score": [60.0, 90.0]},
        geometry=[box(0, 0, 250, 250), box(250, 0, 500, 250)],
        crs="EPSG:2961",
    )
    parcels = gpd.GeoDataFrame(
        {"PID": ["A", "B", "C"], "area_acres": [2.0, 3.0, 0.5], "land_acres": [2.0, 0.8, 0.5]},
        geometry=[box(0, 0, 250, 250), box(250, 0, 500, 250), box(500, 0, 750, 250)],
        crs="EPSG:2961",
    )
    return candidates, parcels


def test_aggregate_leaves_parcels_under_the_minimum_land_area_unranked():
    """SPEC 2.1: a Stage B parcel under the minimum area is excluded. B's better
    cell doesn't save it: it has only 0.8 acres of land (3 mapped)."""
    candidates, parcels = _three_parcels_with_cells()
    result = aggregate_to_parcels(candidates, parcels, min_area_acres=1.0).set_index("PID")
    assert result.loc["A", "rank"] == 1
    assert pd.isna(result.loc["B", "score"]) and pd.isna(result.loc["B", "rank"])
    assert result.loc["B", "cell_score"] == pytest.approx(90.0)  # kept for reference
    assert result.loc["B", "flags"] == "parcel_below_min_area"
    assert result.loc["C", "flags"] == "parcel_no_assigned_candidates; parcel_below_min_area"
    assert result.loc["A", "flags"] == ""


def test_aggregate_minimum_area_off_by_default():
    candidates, parcels = _three_parcels_with_cells()
    result = aggregate_to_parcels(candidates, parcels).set_index("PID")
    assert result.loc["B", "rank"] == 1 and result.loc["A", "rank"] == 2
    assert result.loc["B", "flags"] == ""


def _four_cells_one_parcel():
    """One parcel over four cells; the 4th-best cell has the worst confidence
    and a flag of its own, and the 2nd and 3rd tie on score."""
    candidates = gpd.GeoDataFrame(
        {
            "status": ["eligible"] * 4,
            "score_hydro": [100.0, 60.0, 40.0, 0.0],
            "score_access": [50.0, 20.0, 70.0, 20.0],
            "score_allrounder": [70.0, 30.0, 50.0, 1.0],
            "confidence": [100.0, 85.0, 70.0, 40.0],
            "flags": ["", "access_unverified",
                      "wind_exposure_proxy_only; access_unverified", "coastal_low_elevation"],
        },
        geometry=[box(250 * i, 0, 250 * (i + 1), 250) for i in range(4)],
        crs="EPSG:2961",
    )
    candidates["score"] = 0.6 * candidates["score_hydro"] + 0.4 * candidates["score_access"]
    parcels = gpd.GeoDataFrame({"PID": ["P"]}, geometry=[box(0, 0, 1000, 250)], crs="EPSG:2961")
    return candidates, parcels


def test_parcel_carries_confidence_and_flags_of_its_top_cells():
    """Regression: parcels had no confidence and dropped the flags of the cells
    they rest on (SPEC §2 and §4.4 say to preserve both)."""
    candidates, parcels = _four_cells_one_parcel()
    result = aggregate_to_parcels(candidates, parcels, top_n=3).iloc[0]
    # cells scored 80, 44, 52, 8: the top 3 are cells 0, 2, 1
    assert result["confidence"] == 70.0          # lowest of 100, 70, 85; not cell 3's 40
    assert result["confidence_band"] == "medium"
    assert result["flags"] == "access_unverified; wind_exposure_proxy_only"  # SPEC order, distinct


def test_parcel_top_criterion_means_decompose_cell_score():
    """top_<criterion> = mean of score_<criterion> over the same top-N cells,
    so cell_score = Σ weight × top_<criterion> exactly. Ties at the cut keep
    the earlier cell, as nlargest does."""
    candidates, parcels = _four_cells_one_parcel()
    candidates.loc[1, ["score_hydro", "score_access"]] = [70.0, 25.0]   # 52, ties cell 2
    candidates["score"] = 0.6 * candidates["score_hydro"] + 0.4 * candidates["score_access"]
    for top_n in (1, 2, 3, 10):
        r = aggregate_to_parcels(candidates, parcels, top_n=top_n).iloc[0]
        assert r["cell_score"] == pytest.approx(0.6 * r["top_hydro"] + 0.4 * r["top_access"])
    r = aggregate_to_parcels(candidates, parcels, top_n=2).iloc[0]
    assert (r["top_hydro"], r["top_access"]) == (85.0, 37.5)   # cells 0 and 1, not 2
    assert "top_allrounder" not in aggregate_to_parcels(candidates, parcels).columns


def test_parcel_without_cells_has_no_confidence_or_cell_flags():
    candidates, _ = _four_cells_one_parcel()
    far = gpd.GeoDataFrame({"PID": ["far"]}, geometry=[box(5000, 0, 5500, 250)], crs="EPSG:2961")
    r = aggregate_to_parcels(candidates, far).iloc[0]
    assert pd.isna(r["confidence"]) and r["confidence_band"] is None
    assert pd.isna(r["top_hydro"])
    assert r["flags"] == "parcel_no_assigned_candidates"


def test_severance_flag_appends_to_existing_parcel_flags():
    """classify_parcels keeps an existing flag when it adds the severance one."""
    parcels = _parcels_with_metrics()
    parcels["flags"] = ["parcel_below_min_area", "", ""]
    buildings = gpd.GeoDataFrame(geometry=[box(50, 50, 60, 60)], crs="EPSG:2961")
    result = classify_parcels(parcels, buildings).set_index("PID")
    assert result.loc["60010001", "flags"] == "parcel_below_min_area; severance_candidate"

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


def test_classify_parcels_no_buildings():
    parcels = _parcels_with_metrics()
    result = classify_parcels(parcels, None)
    assert (result["n_buildings"] == 0).all()
    assert (result["parcel_type"] == "land_only").all()
    assert not result["severance_candidate"].any()

"""Tests for preference scoring and parcel aggregation."""

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

from src.scoring.preferences import aggregate_to_parcels, score_parcel_size


def test_score_parcel_size():
    parcels = gpd.GeoDataFrame(
        {"area_acres": [60, 30, 15, 7, 3, 1.5]},
        geometry=[box(i, 0, i + 1, 1) for i in range(6)],
        crs="EPSG:2961",
    )
    scores = score_parcel_size(parcels)
    assert scores.iloc[0] == 100  # 60 acres
    assert scores.iloc[1] == 90   # 30 acres
    assert scores.iloc[2] == 70   # 15 acres
    assert scores.iloc[3] == 40   # 7 acres
    assert scores.iloc[4] == 20   # 3 acres
    assert scores.iloc[5] == 5    # 1.5 acres


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

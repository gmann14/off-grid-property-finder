"""Tests for result export."""

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

from src.export import export_ranked_parcels, export_results


def test_export_creates_files(tmp_path):
    candidates = gpd.GeoDataFrame(
        {
            "status": ["eligible", "excluded"],
            "score": [75.0, None],
            "rank": [1, None],
            "confidence": [90.0, None],
            "confidence_band": ["high", None],
        },
        geometry=[box(380000, 4900000, 380250, 4900250),
                  box(380250, 4900000, 380500, 4900250)],
        crs="EPSG:2961",
    )

    export_results(candidates, tmp_path)

    assert (tmp_path / "scored_cells.csv").exists()
    assert (tmp_path / "scored_cells.geojson").exists()
    assert (tmp_path / "ranked_eligible.csv").exists()

    # Check CSV content
    csv = pd.read_csv(tmp_path / "scored_cells.csv")
    assert len(csv) == 2

    eligible_csv = pd.read_csv(tmp_path / "ranked_eligible.csv")
    assert len(eligible_csv) == 1


def test_export_ranked_parcels(tmp_path):
    parcels = gpd.GeoDataFrame(
        {
            "PID": ["60010001", "60010002", "60010003"],
            "AAN": ["01", "02", "03"],
            "score": [88.0, 40.0, None],          # third has no candidates
            "rank": [1, 2, None],
            "cell_score": [90.0, 35.0, None],
            "size_score": [80.0, 70.0, 60.0],
            "n_cells": [3, 1, 0],
            "area_acres": [42.0, 12.0, 5.0],
            "flags": ["", "", "parcel_no_assigned_candidates"],
        },
        geometry=[box(380000, 4900000, 380250, 4900250),
                  box(380250, 4900000, 380500, 4900250),
                  box(380500, 4900000, 380750, 4900250)],
        crs="EPSG:2961",
    )

    export_ranked_parcels(parcels, tmp_path)

    assert (tmp_path / "ranked_parcels.csv").exists()
    assert (tmp_path / "scored_parcels.geojson").exists()

    csv = pd.read_csv(tmp_path / "ranked_parcels.csv", dtype={"PID": str})
    # Only scored parcels, sorted best-first
    assert len(csv) == 2
    assert csv["PID"].iloc[0] == "60010001"
    assert "latitude" in csv.columns and "longitude" in csv.columns


def test_export_ranked_parcels_none_scored(tmp_path):
    parcels = gpd.GeoDataFrame(
        {"PID": ["60010001"], "score": [None]},
        geometry=[box(380000, 4900000, 380250, 4900250)],
        crs="EPSG:2961",
    )
    # Should not raise; CSV skipped, GeoJSON still written
    export_ranked_parcels(parcels, tmp_path)
    assert not (tmp_path / "ranked_parcels.csv").exists()
    assert (tmp_path / "scored_parcels.geojson").exists()


def test_export_empty_geodataframe(tmp_path):
    candidates = gpd.GeoDataFrame(
        columns=["status", "score", "rank", "geometry"],
        geometry="geometry",
        crs="EPSG:2961",
    )
    # Should not raise
    export_results(candidates, tmp_path)
    assert (tmp_path / "scored_cells.csv").exists()


def _tied_parcels(pids):
    return gpd.GeoDataFrame(
        {"PID": pids, "score": [70.0] * len(pids), "rank": [1] * len(pids)},
        geometry=[box(380000 + 250 * i, 4900000, 380250 + 250 * i, 4900250) for i in range(len(pids))],
        crs="EPSG:2961",
    )


def test_tied_parcels_are_ordered_by_pid(tmp_path):
    """Tied parcels come out in PID order whatever order they arrive in."""
    export_ranked_parcels(_tied_parcels(["60000009", "60000001", "60000005"]), tmp_path)
    csv = pd.read_csv(tmp_path / "ranked_parcels.csv", dtype={"PID": str})
    assert csv["PID"].tolist() == ["60000001", "60000005", "60000009"]


def test_tie_order_does_not_depend_on_other_rows(tmp_path):
    """Adding or removing an unrelated, lower-ranked parcel must not reorder a tie
    (it did with the default quicksort, so a top-N cut at a tie could change)."""
    pids = [f"6{i:07d}" for i in range(40, 0, -1)]
    tied = _tied_parcels(pids)
    extra = _tied_parcels(["69999999"]).assign(score=10.0, rank=41)
    export_ranked_parcels(tied, tmp_path / "a")
    export_ranked_parcels(pd.concat([extra, tied], ignore_index=True), tmp_path / "b")
    a = pd.read_csv(tmp_path / "a" / "ranked_parcels.csv", dtype={"PID": str})
    b = pd.read_csv(tmp_path / "b" / "ranked_parcels.csv", dtype={"PID": str})
    assert b["PID"].head(40).tolist() == a["PID"].tolist() == sorted(pids)

"""Tests for result export."""

import geopandas as gpd
import pandas as pd
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


def test_export_ranked_parcels_none_scored_replaces_the_old_list(tmp_path):
    """Regression: with no scored parcels the CSV was skipped, so the previous
    run's ranked_parcels.csv stayed in output/ for visualize and snapshot."""
    pd.DataFrame({"PID": ["old"], "score": [99.0]}).to_csv(tmp_path / "ranked_parcels.csv", index=False)
    parcels = gpd.GeoDataFrame(
        {"PID": ["60010001"], "score": [None], "rank": [None]},
        geometry=[box(380000, 4900000, 380250, 4900250)],
        crs="EPSG:2961",
    )
    export_ranked_parcels(parcels, tmp_path)
    csv = pd.read_csv(tmp_path / "ranked_parcels.csv")
    assert csv.empty and {"PID", "score", "rank"} <= set(csv.columns)
    assert (tmp_path / "scored_parcels.geojson").exists()


def test_export_no_eligible_cells_replaces_the_old_ranked_list(tmp_path):
    """Regression: with no eligible cells ranked_eligible.csv was skipped, so
    the previous run's stayed in output/."""
    pd.DataFrame({"score": [99.0]}).to_csv(tmp_path / "ranked_eligible.csv", index=False)
    candidates = gpd.GeoDataFrame(
        {"status": ["excluded"], "score": [None], "rank": [None]},
        geometry=[box(380000, 4900000, 380250, 4900250)],
        crs="EPSG:2961",
    )
    export_results(candidates, tmp_path)
    csv = pd.read_csv(tmp_path / "ranked_eligible.csv")
    assert csv.empty and {"status", "score", "rank"} <= set(csv.columns)


def test_remove_stale_parcel_outputs(tmp_path):
    from src.export import PARCEL_OUTPUTS, remove_stale_parcel_outputs

    for name in PARCEL_OUTPUTS:
        (tmp_path / name).write_text("old run")
    (tmp_path / "scored_cells.csv").write_text("keep")
    assert len(remove_stale_parcel_outputs(tmp_path)) == len(PARCEL_OUTPUTS)
    assert not any((tmp_path / n).exists() for n in PARCEL_OUTPUTS)
    assert (tmp_path / "scored_cells.csv").exists()
    assert remove_stale_parcel_outputs(tmp_path) == []  # nothing left: no-op


def test_export_empty_geodataframe(tmp_path):
    candidates = gpd.GeoDataFrame(
        columns=["status", "score", "rank", "geometry"],
        geometry="geometry",
        crs="EPSG:2961",
    )
    # Should not raise
    export_results(candidates, tmp_path)
    assert (tmp_path / "scored_cells.csv").exists()


def test_parcel_csv_adds_confidence_and_top_criteria_after_the_existing_columns(tmp_path):
    """The new parcel columns come after latitude/longitude, so the existing
    columns keep their positions for anything reading the CSV by position."""
    from src.export import PARCEL_CSV_COLUMNS

    parcels = gpd.GeoDataFrame(
        {"PID": ["60010001"], "score": [70.0], "rank": [1], "flags": [""],
         "top_hydro": [80.0], "confidence_band": ["high"], "confidence": [85.0],
         "top_access": [50.0]},
        geometry=[box(380000, 4900000, 380250, 4900250)],
        crs="EPSG:2961",
    )
    export_ranked_parcels(parcels, tmp_path)
    cols = pd.read_csv(tmp_path / "ranked_parcels.csv").columns.tolist()
    existing = [c for c in PARCEL_CSV_COLUMNS if c in parcels.columns]
    assert cols == existing + ["latitude", "longitude",
                               "confidence", "confidence_band", "top_hydro", "top_access"]


def test_cell_csvs_lead_with_cell_id(tmp_path):
    """Regression: scored_cells.csv and ranked_eligible.csv had no cell_id, so
    a row couldn't be joined back to scored_cells.gpkg."""
    candidates = gpd.GeoDataFrame(
        {"cell_id": [7, 9], "status": ["eligible", "eligible"],
         "score": [40.0, 75.0], "rank": [2, 1]},
        geometry=[box(380000, 4900000, 380250, 4900250),
                  box(380250, 4900000, 380500, 4900250)],
        crs="EPSG:2961",
    )
    export_results(candidates, tmp_path)
    cells = pd.read_csv(tmp_path / "scored_cells.csv")
    ranked = pd.read_csv(tmp_path / "ranked_eligible.csv")
    assert cells.columns[0] == "cell_id" and cells["cell_id"].tolist() == [7, 9]
    assert ranked.columns[0] == "cell_id" and ranked["cell_id"].tolist() == [9, 7]


def test_cell_id_index_is_exported_as_the_first_column(tmp_path, small_grid):
    candidates = small_grid.assign(status="eligible", score=50.0, rank=1)
    assert candidates.index.name == "cell_id"
    export_results(candidates, tmp_path)
    cells = pd.read_csv(tmp_path / "scored_cells.csv")
    assert cells.columns[0] == "cell_id"
    assert cells["cell_id"].tolist() == small_grid.index.tolist()


def test_export_without_status_column_treats_cells_as_eligible(tmp_path):
    """Regression: ``candidates[candidates.get("status") != "excluded"]`` is
    ``candidates[True]`` when the column is missing, a KeyError."""
    candidates = gpd.GeoDataFrame(
        {"score": [75.0, 60.0], "rank": [1, 2]},
        geometry=[box(380000, 4900000, 380250, 4900250),
                  box(380250, 4900000, 380500, 4900250)],
        crs="EPSG:2961",
    )
    export_results(candidates, tmp_path)
    assert len(pd.read_csv(tmp_path / "ranked_eligible.csv")) == 2


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

"""Tests for the results/ snapshot builder."""

import os

import pandas as pd
import pytest

from src.snapshot import SNAPSHOT_COLUMNS, TOP_LAND_ONLY_N, SnapshotError, build_snapshot


def _ranked(n=4, **overrides):
    df = pd.DataFrame({
        "PID": [f"0{i:07d}" for i in range(n)],   # leading zeros must survive
        "rank": list(range(1, n + 1)),
        "score": [90.0 - i for i in range(n)],
        "cell_score": [80.0] * n,
        "size_score": [60.0] * n,
        "n_cells": [2] * n,
        "area_acres": [12.5] * n,
        "land_acres": [12.0] * n,
        "water_share": [0.04] * n,
        "parcel_type": ["land_only", "developed"] * (n // 2) + ["land_only"] * (n % 2),
        "n_buildings": [0, 3] * (n // 2) + [0] * (n % 2),
        "severance_candidate": [False, True] * (n // 2) + [False] * (n % 2),
        "flags": [""] * n,
        "latitude": [44.123456789] * n,
        "longitude": [-64.987654321] * n,
    })
    for k, v in overrides.items():
        df[k] = v
    return df


def _write(tmp_path, df):
    path = tmp_path / "ranked_parcels.csv"
    df.to_csv(path, index=False)
    return path


def test_snapshot_writes_three_files_with_snapshot_columns(tmp_path):
    counts = build_snapshot(_write(tmp_path, _ranked()), tmp_path / "results")
    assert counts == {"ranked": 4, "top_land_only": 2, "severance_candidates": 2}
    ranked = pd.read_csv(tmp_path / "results" / "ranked_pids.csv", dtype={"PID": str})
    assert list(ranked.columns) == SNAPSHOT_COLUMNS + ["maps_link"]
    assert ranked["PID"].tolist() == ["00000000", "00000001", "00000002", "00000003"]
    assert ranked["maps_link"][0] == "https://www.google.com/maps?q=44.123457,-64.987654"
    sev = pd.read_csv(tmp_path / "results" / "severance_candidates.csv", dtype={"PID": str})
    assert sev["PID"].tolist() == ["00000001", "00000003"]


def test_snapshot_sorts_by_rank_and_keeps_export_order_inside_ties(tmp_path):
    df = _ranked()
    df["rank"] = [2, 1, 2, 1]          # export order inside each tie: PID order
    build_snapshot(_write(tmp_path, df), tmp_path / "out")
    ranked = pd.read_csv(tmp_path / "out" / "ranked_pids.csv", dtype={"PID": str})
    assert ranked["PID"].tolist() == ["00000001", "00000003", "00000000", "00000002"]


def test_snapshot_caps_top_land_only(tmp_path):
    df = _ranked(n=TOP_LAND_ONLY_N * 3)
    df["parcel_type"] = "land_only"
    counts = build_snapshot(_write(tmp_path, df), tmp_path / "out")
    assert counts["top_land_only"] == TOP_LAND_ONLY_N


def test_snapshot_with_no_land_only_or_severance_writes_header_only_files(tmp_path):
    df = _ranked(parcel_type="developed", severance_candidate=False)
    counts = build_snapshot(_write(tmp_path, df), tmp_path / "out")
    assert counts["top_land_only"] == 0 and counts["severance_candidates"] == 0
    for name in ("top_land_only.csv", "severance_candidates.csv"):
        assert pd.read_csv(tmp_path / "out" / name).empty


def test_snapshot_leaves_maps_link_blank_without_coordinates(tmp_path):
    df = _ranked()
    df.loc[1, "latitude"] = None
    build_snapshot(_write(tmp_path, df), tmp_path / "out")
    ranked = pd.read_csv(tmp_path / "out" / "ranked_pids.csv", keep_default_na=False)
    assert ranked["maps_link"][1] == ""
    assert ranked["maps_link"][0].startswith("https://")


def test_snapshot_reads_string_booleans(tmp_path):
    df = _ranked(severance_candidate=["False", "True", "false", "TRUE"])
    counts = build_snapshot(_write(tmp_path, df), tmp_path / "out")
    assert counts["severance_candidates"] == 2


def test_snapshot_refuses_a_missing_export(tmp_path):
    with pytest.raises(SnapshotError, match="not found"):
        build_snapshot(tmp_path / "nope.csv", tmp_path / "out")


def test_snapshot_refuses_an_empty_file(tmp_path):
    path = tmp_path / "ranked_parcels.csv"
    path.write_text("")
    with pytest.raises(SnapshotError, match="empty"):
        build_snapshot(path, tmp_path / "out")


def test_snapshot_refuses_header_only_export(tmp_path):
    path = _write(tmp_path, _ranked().iloc[0:0])
    with pytest.raises(SnapshotError, match="no ranked parcels"):
        build_snapshot(path, tmp_path / "out")


def test_snapshot_refuses_missing_columns(tmp_path):
    path = _write(tmp_path, _ranked().drop(columns=["land_acres"]))
    with pytest.raises(SnapshotError, match="land_acres"):
        build_snapshot(path, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_snapshot_refuses_an_export_older_than_the_parcel_gpkg(tmp_path):
    csv = _write(tmp_path, _ranked())
    gpkg = tmp_path / "scored_parcels.gpkg"
    gpkg.write_text("x")
    old = csv.stat().st_mtime - 60
    os.utime(csv, (old, old))
    with pytest.raises(SnapshotError, match="did not finish"):
        build_snapshot(csv, tmp_path / "out", scored_parcels=gpkg)
    os.utime(gpkg, (old - 60, old - 60))
    assert build_snapshot(csv, tmp_path / "out", scored_parcels=gpkg)["ranked"] == 4

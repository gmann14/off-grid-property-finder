"""Tests for result export."""

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

from src.export import export_results


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


def test_export_empty_geodataframe(tmp_path):
    candidates = gpd.GeoDataFrame(
        columns=["status", "score", "rank", "geometry"],
        geometry="geometry",
        crs="EPSG:2961",
    )
    # Should not raise
    export_results(candidates, tmp_path)
    assert (tmp_path / "scored_cells.csv").exists()

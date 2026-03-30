"""Tests for the analyze command and score distribution analysis."""

import logging

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from click.testing import CliRunner
from shapely.geometry import box

from src.analyze import (
    _format_confidence_bands,
    _format_histogram,
    _format_stats,
    run_analyze,
)
from src.cli import cli
from src.config import Config, Paths, StudyArea


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def scored_gdf():
    """Create a synthetic scored GeoDataFrame with known distribution."""
    n = 100
    rng = np.random.RandomState(42)
    geom = [box(i, i, i + 1, i + 1) for i in range(n)]

    data = {
        "score_hydro": np.concatenate([np.zeros(30), rng.uniform(10, 90, 40), np.full(30, 100)]),
        "score_solar": np.full(n, 80.0),
        "score_elevation": rng.uniform(0, 100, n),
        "score_access": np.concatenate([np.full(50, 100), np.full(50, 0)]),
        "score_buildable": rng.uniform(20, 100, n),
        "score": rng.uniform(0, 100, n),
        "confidence": np.concatenate([np.full(60, 80.0), np.full(25, 65.0), np.full(15, 40.0)]),
        "confidence_band": (["high"] * 60 + ["medium"] * 25 + ["low"] * 15),
        "status": ["eligible"] * n,
    }

    return gpd.GeoDataFrame(data, geometry=geom, crs="EPSG:2961")


@pytest.fixture
def output_with_scored(tmp_path, scored_gdf):
    """Write scored_cells.gpkg to a temp output dir."""
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    scored_gdf.to_file(output_dir / "scored_cells.gpkg", driver="GPKG")
    return output_dir


@pytest.fixture
def config_for_analyze(tmp_path, output_with_scored):
    """Config pointing to the temp output dir."""
    return Config(
        study_area=StudyArea(bbox=(0, 0, 100, 100), name="test"),
        paths=Paths(
            raw=tmp_path / "raw",
            processed=tmp_path / "processed",
            output=output_with_scored,
        ),
    )


class TestFormatStats:
    def test_basic_stats(self):
        series = pd.Series([0, 25, 50, 75, 100])
        result = _format_stats(series)
        assert "Count: 5" in result
        assert "Min: 0.0" in result
        assert "Max: 100.0" in result
        assert "Mean: 50.0" in result
        assert "Median: 50.0" in result

    def test_percentiles_present(self):
        series = pd.Series(range(101))
        result = _format_stats(series)
        assert "P10:" in result
        assert "P25:" in result
        assert "P50:" in result
        assert "P75:" in result
        assert "P90:" in result

    def test_count_at_extremes(self):
        series = pd.Series([0, 0, 0, 50, 100, 100])
        result = _format_stats(series)
        assert "Cells at 100: 2 (33.3%)" in result
        assert "Cells at 0: 3 (50.0%)" in result

    def test_empty_series(self):
        result = _format_stats(pd.Series(dtype=float))
        assert "No valid data" in result

    def test_nan_values_ignored(self):
        series = pd.Series([10, 20, np.nan, 30])
        result = _format_stats(series)
        assert "Count: 3" in result


class TestFormatHistogram:
    def test_histogram_has_correct_bins(self):
        series = pd.Series(range(101))
        result = _format_histogram(series, bins=10)
        # Should have 10 lines (one per bin)
        lines = [l for l in result.strip().split("\n") if "|" in l]
        assert len(lines) == 10

    def test_histogram_empty_series(self):
        result = _format_histogram(pd.Series(dtype=float))
        assert "No valid data" in result

    def test_histogram_shows_counts(self):
        # All values in first bin
        series = pd.Series([5.0] * 50)
        result = _format_histogram(series, bins=10)
        assert "50" in result

    def test_histogram_range_0_to_100(self):
        series = pd.Series([0, 50, 100])
        result = _format_histogram(series, bins=10)
        assert "0" in result
        assert "100" in result


class TestFormatConfidenceBands:
    def test_band_counts(self):
        gdf = gpd.GeoDataFrame(
            {"confidence_band": ["high", "high", "medium", "low"]},
            geometry=[box(0, 0, 1, 1)] * 4,
        )
        result = _format_confidence_bands(gdf)
        assert "high: 2 (50.0%)" in result
        assert "medium: 1 (25.0%)" in result
        assert "low: 1 (25.0%)" in result

    def test_missing_column(self):
        gdf = gpd.GeoDataFrame(
            {"other": [1]},
            geometry=[box(0, 0, 1, 1)],
        )
        result = _format_confidence_bands(gdf)
        assert "column found" in result

    def test_nan_values_handled(self):
        gdf = gpd.GeoDataFrame(
            {"confidence_band": ["high", None, "low"]},
            geometry=[box(0, 0, 1, 1)] * 3,
        )
        result = _format_confidence_bands(gdf)
        assert "high: 1 (50.0%)" in result
        assert "low: 1 (50.0%)" in result


class TestRunAnalyze:
    def test_produces_report(self, config_for_analyze):
        report = run_analyze(config_for_analyze, logging.getLogger("test"))
        assert "score_hydro" in report
        assert "score_solar" in report
        assert "score_elevation" in report
        assert "score_access" in report
        assert "score_buildable" in report
        assert "score" in report
        assert "confidence" in report

    def test_report_includes_histogram(self, config_for_analyze):
        report = run_analyze(config_for_analyze, logging.getLogger("test"))
        assert "#" in report  # histogram bars

    def test_report_includes_confidence_bands(self, config_for_analyze):
        report = run_analyze(config_for_analyze, logging.getLogger("test"))
        assert "high:" in report
        assert "medium:" in report
        assert "low:" in report

    def test_missing_gpkg_raises(self, tmp_path):
        cfg = Config(
            study_area=StudyArea(bbox=(0, 0, 1, 1), name="test"),
            paths=Paths(
                raw=tmp_path / "raw",
                processed=tmp_path / "processed",
                output=tmp_path / "empty_output",
            ),
        )
        with pytest.raises(FileNotFoundError, match="scored_cells.gpkg"):
            run_analyze(cfg, logging.getLogger("test"))

    def test_cells_at_100_reported(self, config_for_analyze):
        report = run_analyze(config_for_analyze, logging.getLogger("test"))
        # score_hydro has 30 cells at 100 out of 100
        assert "Cells at 100: 30 (30.0%)" in report

    def test_cells_at_0_reported(self, config_for_analyze):
        report = run_analyze(config_for_analyze, logging.getLogger("test"))
        # score_hydro has 30 cells at 0
        assert "Cells at 0: 30 (30.0%)" in report


class TestAnalyzeCLI:
    def test_analyze_help(self, runner):
        result = runner.invoke(cli, ["analyze", "--help"])
        assert result.exit_code == 0
        assert "score distribution" in result.output.lower()

    def test_analyze_missing_config(self, runner, tmp_path):
        result = runner.invoke(cli, ["--config", str(tmp_path / "nope.yaml"), "analyze"])
        assert result.exit_code != 0

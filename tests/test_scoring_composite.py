"""Tests for composite scoring and the registry."""

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

from src.config import Config, StudyArea
from src.scoring.registry import (
    _REGISTRY,
    available_scorers,
    compute_composite_score,
    get_scorer,
    register,
)


def test_register_and_get():
    @register("_test_metric")
    def _test(candidates, config):
        return pd.Series(50.0, index=candidates.index)

    assert "_test_metric" in available_scorers()
    assert get_scorer("_test_metric") is _test

    # Cleanup
    del _REGISTRY["_test_metric"]


def test_get_unknown_scorer():
    with pytest.raises(KeyError, match="Unknown scoring metric"):
        get_scorer("nonexistent_metric")


def test_composite_score_basic():
    # Register two simple scorers
    @register("_test_a")
    def _a(candidates, config):
        return pd.Series(100.0, index=candidates.index)

    @register("_test_b")
    def _b(candidates, config):
        return pd.Series(50.0, index=candidates.index)

    grid = gpd.GeoDataFrame(
        {"status": ["eligible", "eligible"]},
        geometry=[box(0, 0, 1, 1), box(1, 0, 2, 1)],
        crs="EPSG:2961",
    )

    cfg = Config(
        study_area=StudyArea(bbox=(0, 0, 2, 1)),
        weights={"_test_a": 60, "_test_b": 40},
        enabled_criteria=["_test_a", "_test_b"],
    )

    result = compute_composite_score(grid, cfg)
    # Score = 100*(60/100) + 50*(40/100) = 60 + 20 = 80
    assert result["score"].iloc[0] == pytest.approx(80.0, abs=0.1)

    # Cleanup
    del _REGISTRY["_test_a"]
    del _REGISTRY["_test_b"]


def test_allrounder_penalizes_imbalance():
    # Two criteria. Cell A is balanced (70/70); cell B is lopsided (100/10).
    # Composite (equal weights) is the same mean (70 vs 55), but the all-rounder
    # geometric mean must rank the balanced cell higher.
    @register("_ar_a")
    def _a(c, cfg):
        return pd.Series([70.0, 100.0], index=c.index)

    @register("_ar_b")
    def _b(c, cfg):
        return pd.Series([70.0, 10.0], index=c.index)

    grid = gpd.GeoDataFrame(
        {"status": ["eligible", "eligible"]},
        geometry=[box(0, 0, 1, 1), box(1, 0, 2, 1)],
        crs="EPSG:2961",
    )
    cfg = Config(
        study_area=StudyArea(bbox=(0, 0, 2, 1)),
        weights={"_ar_a": 50, "_ar_b": 50},
        enabled_criteria=["_ar_a", "_ar_b"],
    )

    result = compute_composite_score(grid, cfg)
    assert "score_allrounder" in result.columns
    # balanced 70/70 -> 70; lopsided 100/10 -> sqrt(1000) ~= 31.6
    assert result["score_allrounder"].iloc[0] == pytest.approx(70.0, abs=0.5)
    assert result["score_allrounder"].iloc[1] == pytest.approx(31.6, abs=1.0)
    assert result["score_allrounder"].iloc[0] > result["score_allrounder"].iloc[1]

    del _REGISTRY["_ar_a"]
    del _REGISTRY["_ar_b"]


def test_excluded_cells_get_null_score():
    @register("_test_c")
    def _c(candidates, config):
        return pd.Series(75.0, index=candidates.index)

    grid = gpd.GeoDataFrame(
        {"status": ["eligible", "excluded"]},
        geometry=[box(0, 0, 1, 1), box(1, 0, 2, 1)],
        crs="EPSG:2961",
    )

    cfg = Config(
        study_area=StudyArea(bbox=(0, 0, 2, 1)),
        weights={"_test_c": 100},
        enabled_criteria=["_test_c"],
    )

    result = compute_composite_score(grid, cfg)
    assert pd.notna(result["score"].iloc[0])
    assert pd.isna(result["score"].iloc[1])

    del _REGISTRY["_test_c"]

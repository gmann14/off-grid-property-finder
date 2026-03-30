"""Tests for micro-hydro scoring."""

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
import rasterio
from rasterio.transform import from_bounds
from shapely.geometry import LineString, box

from src.scoring.hydro import _estimate_flow_rate, _estimate_power, _lookup_score, score_hydro
from src.constants import HYDRO_POWER_THRESHOLDS, MIN_DRAINAGE_AREA_KM2, SPECIFIC_RUNOFF_LOW


def test_estimate_flow_rate():
    # 1 km² at 8 L/s/km² (HYDAT-calibrated) = 0.008 m³/s
    flow = _estimate_flow_rate(1.0)
    assert abs(flow - SPECIFIC_RUNOFF_LOW / 1000.0) < 1e-6


def test_estimate_flow_rate_large_area():
    flow = _estimate_flow_rate(10.0)
    assert abs(flow - 10.0 * SPECIFIC_RUNOFF_LOW / 1000.0) < 1e-6


def test_estimate_power():
    # P = 0.5 * 1000 * 9.81 * 0.01 * 10 = 490.5 W
    power = _estimate_power(0.01, 10.0, efficiency=0.5)
    assert abs(power - 490.5) < 1


def test_estimate_power_zero_head():
    power = _estimate_power(0.01, 0.0)
    assert power == 0.0


def test_lookup_hydro_high_power():
    assert _lookup_score(2500, HYDRO_POWER_THRESHOLDS) == 100


def test_lookup_hydro_medium_power():
    assert _lookup_score(1500, HYDRO_POWER_THRESHOLDS) == 80


def test_lookup_hydro_low_power():
    assert _lookup_score(30, HYDRO_POWER_THRESHOLDS) == 0


def test_waco_connector_no_false_hydro(tmp_path):
    """WACO connectors at sea level must not score for hydro.

    Regression test: short WACO connector fragments with no DEM data
    were picking up cell terrain relief as head, giving tidal connectors
    false hydro scores.
    """
    CRS = "EPSG:2961"
    xmin, ymin = 380000.0, 4900000.0
    cell_size = 250

    # One cell
    cell_geom = box(xmin, ymin, xmin + cell_size, ymin + cell_size)
    candidates = gpd.GeoDataFrame(
        {"cell_id": [0]},
        geometry=[cell_geom],
        crs=CRS,
    )

    # Short WACO connector (6m) at sea level — mimics tidal connector
    stream_start = (xmin + 100, ymin + 125)
    stream_end = (xmin + 106, ymin + 125)
    streams = gpd.GeoDataFrame(
        {
            "FEAT_CODE": ["WACO25"],
            "LINE_CLASS": [3],
        },
        geometry=[LineString([stream_start, stream_end])],
        crs=CRS,
    )
    streams_path = tmp_path / "streams.gpkg"
    streams.to_file(streams_path, driver="GPKG")

    # Flat DEM at ~2m (sea level) with some terrain relief in the cell
    dem_path = tmp_path / "dem.tif"
    width, height = 25, 25
    transform = from_bounds(xmin, ymin, xmin + cell_size, ymin + cell_size, width, height)
    # Create terrain with 5m relief but the stream area is flat at 2m
    dem = np.full((height, width), 2.0, dtype=np.float32)
    dem[:5, :] = 7.0  # higher terrain at north edge of cell
    with rasterio.open(
        dem_path, "w", driver="GTiff",
        height=height, width=width, count=1, dtype="float32",
        crs=CRS, transform=transform, nodata=-9999,
    ) as dst:
        dst.write(dem, 1)

    from src.config import Config, Paths, StudyArea
    config = Config(
        study_area=StudyArea(bbox=(xmin, ymin, xmin + cell_size, ymin + cell_size), name="test"),
        cell_size_m=cell_size,
        paths=Paths(raw=tmp_path / "raw", processed=tmp_path, output=tmp_path / "output"),
    )

    scores = score_hydro(candidates, config)
    assert scores.iloc[0] == 0, f"WACO connector should not score for hydro, got {scores.iloc[0]}"

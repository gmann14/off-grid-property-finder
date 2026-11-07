"""Tests for micro-hydro scoring."""

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
import rasterio
from rasterio.transform import from_bounds
from shapely.geometry import LineString, box

from src.scoring.bands import band_score
from src.scoring.hydro import _estimate_flow_rate, _estimate_power, score_hydro
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
    assert band_score(2500, HYDRO_POWER_THRESHOLDS) == 100


def test_lookup_hydro_medium_power():
    assert band_score(1500, HYDRO_POWER_THRESHOLDS) == 80


def test_lookup_hydro_low_power():
    assert band_score(30, HYDRO_POWER_THRESHOLDS) == 0


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


def _hydro_config(tmp_path, bbox, cell_size=250):
    from src.config import Config, Paths, StudyArea
    return Config(
        study_area=StudyArea(bbox=bbox, name="test"),
        cell_size_m=cell_size,
        paths=Paths(raw=tmp_path / "raw", processed=tmp_path, output=tmp_path / "output"),
    )


def _write_raster(path, bounds, arr, nodata=-9999, dtype="float32"):
    height, width = arr.shape
    transform = from_bounds(*bounds, width, height)
    with rasterio.open(
        path, "w", driver="GTiff", height=height, width=width, count=1,
        dtype=dtype, crs="EPSG:2961", transform=transform, nodata=nodata,
    ) as dst:
        dst.write(arr.astype(dtype), 1)


def test_filter_drops_coastline_codes_and_line_class_3():
    """WACO* (coastline, tidal river banks/centrelines, closing lines) and
    LINE_CLASS 3 (coastline class) are tidal water, not streams."""
    from src.scoring.hydro import _filter_to_flowing_water

    codes = ["WACO20", "WACO25", "WACOF0", "WACORV20", "WACORV25", "WACORV59",
             "WACORVF0", "WARV50", "WARV59", "WARV10", "WAXX99"]
    classes = [3, 3, 6, 2, 2, 1, 6, 1, 1, 2, 3]
    streams = gpd.GeoDataFrame(
        {"FEAT_CODE": codes, "LINE_CLASS": classes},
        geometry=[LineString([(0, i), (100, i)]) for i in range(len(codes))],
        crs="EPSG:2961",
    )
    kept = _filter_to_flowing_water(streams)
    assert sorted(kept["FEAT_CODE"]) == ["WARV10", "WARV50", "WARV59"]


def test_drainage_guess_has_no_coastline_boost():
    """The segment-length drainage guess must not inflate a 100 m line to
    25 km² (LINE_CLASS 3) or 10 km² (WACO)."""
    from src.scoring.hydro import _estimate_drainage_area

    line = LineString([(0, 0), (100, 0)])
    for code, lc in (("WACO20", 3), ("WACORV59", 1), ("WARV50", 3)):
        stream = pd.Series({"FEAT_CODE": code, "LINE_CLASS": lc, "geometry": line})
        assert _estimate_drainage_area(stream, None) == MIN_DRAINAGE_AREA_KM2


def test_coastline_next_to_a_bluff_scores_no_hydro(tmp_path):
    """Regression (cells 27585 / 27289): a WACO coastline line drawn across
    the foot of a coastal bluff picked up the bluff as 'head' and scored."""
    xmin, ymin = 380000.0, 4900000.0
    bounds = (xmin, ymin, xmin + 250, ymin + 250)
    candidates = gpd.GeoDataFrame({"cell_id": [0]}, geometry=[box(*bounds)], crs="EPSG:2961")
    coast = LineString([(xmin + 10, ymin + 10), (xmin + 240, ymin + 240)])
    gpd.GeoDataFrame(
        {"FEAT_CODE": ["WACO20"], "LINE_CLASS": [3], "MINZ": [0.2], "MAXZ": [0.2]},
        geometry=[coast], crs="EPSG:2961",
    ).to_file(tmp_path / "streams.gpkg", driver="GPKG")
    # Sea at 0 m in the south-west rising to a 30 m bluff top in the north-east.
    yy, xx = np.mgrid[0:25, 0:25]
    dem = np.clip((xx + (24 - yy)) * 1.5 - 10, 0, 30)
    _write_raster(tmp_path / "dem.tif", bounds, dem)

    scores = score_hydro(candidates, _hydro_config(tmp_path, bounds))
    assert scores.iloc[0] == 0


def _flat_dem_brook(tmp_path):
    """A 1 km cell-crossing brook on a flat DEM, whose MINZ/MAXZ attributes
    claim 20 m of drop: 0 with the DEM, but 80 if head falls back to MINZ/MAXZ.

    A second brook, 2 km away and off the DEM, needs the fallback head (and so
    the per-cell elevation-range precompute)."""
    xmin, ymin = 380000.0, 4900000.0
    bounds = (xmin, ymin, xmin + 1000, ymin + 1000)
    cell = box(xmin + 375, ymin + 375, xmin + 625, ymin + 625)
    candidates = gpd.GeoDataFrame({"cell_id": [0]}, geometry=[cell], crs="EPSG:2961")
    gpd.GeoDataFrame(
        {"FEAT_CODE": ["WARV50", "WARV50"], "LINE_CLASS": [1, 1], "FLOWDIR": [1, 1],
         "MINZ": [10.0, 10.0], "MAXZ": [30.0, 30.0]},
        geometry=[LineString([(xmin, ymin + 500), (xmin + 1000, ymin + 500)]),
                  LineString([(xmin + 3000, ymin + 500), (xmin + 4000, ymin + 500)])],
        crs="EPSG:2961",
    ).to_file(tmp_path / "streams.gpkg", driver="GPKG")
    _write_raster(tmp_path / "dem.tif", bounds, np.full((100, 100), 20.0))
    return candidates, _hydro_config(tmp_path, bounds)


def test_failed_cell_range_precompute_is_logged_and_keeps_dem_head(tmp_path, monkeypatch, caplog):
    """Regression: an exception in the per-cell range precompute set
    has_dem=False silently, so head came from MINZ/MAXZ (score 80) instead of
    the DEM (flat: score 0)."""
    import logging

    import src.zonal

    candidates, config = _flat_dem_brook(tmp_path)
    assert score_hydro(candidates, config).iloc[0] == 0

    def boom(*args, **kwargs):
        raise MemoryError("label raster too big")

    monkeypatch.setattr(src.zonal, "grid_zonal_stats", boom)
    with caplog.at_level(logging.WARNING, logger="property_finder"):
        score = score_hydro(candidates, config).iloc[0]
    assert score == 0
    assert "label raster too big" in caplog.text
    assert "precompute failed" in caplog.text


def test_unreadable_dem_is_logged(tmp_path, caplog):
    import logging

    candidates, config = _flat_dem_brook(tmp_path)
    (tmp_path / "dem.tif").write_bytes(b"not a tiff")
    with caplog.at_level(logging.WARNING, logger="property_finder"):
        score_hydro(candidates, config)
    assert "could not open DEM" in caplog.text


# --- drainage read along the stream's own line (H1) -------------------------
X0, Y0 = 380000.0, 4900000.0
FACC_BOUNDS = (X0, Y0 - 500, X0 + 1000, Y0 + 500)  # 50 x 50 px of 20 m
BROOK_CELLS = 60000  # 60,000 px of 400 m² = 24 km²


def _wandering_brook(tmp_path, channel_rows=None, line=None, flowdir=1, dem_fn=None):
    """A 24 km² routed channel 60 m north of the mapped brook line, except
    for 40 m around x = X0+400 where the line crosses it.

    The mapped brook runs west -> east (digitised downstream) at y = Y0+10,
    dropping 2 m per 100 m. The cell's centre is 50 m east of the crossing.
    """
    facc = np.full((50, 50), 10.0)
    if channel_rows is None:
        channel_rows = {c: 21 for c in range(50)}       # y in [Y0+60, Y0+80)
        for c in (19, 20):                              # x in [X0+380, X0+420)
            channel_rows[c] = 24                        # y in [Y0, Y0+20): on the line
    for c, r in channel_rows.items():
        facc[r, c] = BROOK_CELLS
    _write_raster(tmp_path / "flow_accumulation.tif", FACC_BOUNDS, facc, nodata=-32768)
    xs = np.arange(5.0, 1000, 10.0)  # 10 m DEM pixel centres, metres east of X0
    dem_fn = dem_fn or (lambda x: 100.0 - 0.02 * x)
    _write_raster(tmp_path / "dem.tif", FACC_BOUNDS, np.tile(dem_fn(xs), (100, 1)))
    line = line or LineString([(X0, Y0 + 10), (X0 + 1000, Y0 + 10)])
    gpd.GeoDataFrame(
        {"FEAT_CODE": ["WARV50"], "LINE_CLASS": [1], "FLOWDIR": [flowdir],
         "MINZ": [80.0], "MAXZ": [100.0]},
        geometry=[line], crs="EPSG:2961",
    ).to_file(tmp_path / "streams.gpkg", driver="GPKG")
    cx, cy = X0 + 450, Y0 + 60
    cell = box(cx - 125, cy - 125, cx + 125, cy + 125)
    candidates = gpd.GeoDataFrame({"cell_id": [0]}, geometry=[cell], crs="EPSG:2961")
    return candidates, _hydro_config(tmp_path, FACC_BOUNDS)


def test_drainage_is_read_along_the_line_not_at_one_point(tmp_path):
    """Regression (cells 1214, 1862): the single 3x3 read at the point nearest
    the cell centre missed the routed channel (0.004 km² < 0.5 km² minimum),
    so a 24 km² brook with 10 m+ of head scored 0."""
    candidates, config = _wandering_brook(tmp_path)
    assert score_hydro(candidates, config).iloc[0] == 100


def test_drainage_read_skips_the_receiving_river_at_the_junction(tmp_path):
    """A tributary whose only big read is at its downstream end (where it
    meets its receiving river) must not take the receiving river's drainage."""
    from src.scoring.hydro import _DrainageGrid, _drainage_profile, _line_samples

    _wandering_brook(tmp_path, channel_rows={49: 24})  # x in [X0+980, X0+1000)
    line = LineString([(X0, Y0 + 10), (X0 + 1000, Y0 + 10)])
    s, xy = _line_samples(line)
    with rasterio.open(tmp_path / "flow_accumulation.tif") as src:
        grid = _DrainageGrid(src)
    # drawn in its flow direction (FLOWDIR 1): the junction end is not read
    assert np.nanmax(_drainage_profile(grid, s, xy, line.length, 1)) < MIN_DRAINAGE_AREA_KM2
    # direction unknown (FLOWDIR 0): the end is kept
    prof = _drainage_profile(grid, s, xy, line.length, 0)
    assert prof[-1] == pytest.approx(24.0)
    assert prof[s < 880].max() < MIN_DRAINAGE_AREA_KM2  # only within 60 m of it


# --- head from SPEC §3.1 intake/outfall pairs (H2 + H3) ---------------------
ON_LINE = {c: 24 for c in range(50)}  # 24 km² read all along the brook


def test_dem_undulation_on_a_flat_reach_is_not_head(tmp_path):
    """Regression: head was max - min of ~21 samples in any order, so ±2 m of
    DEM undulation along a flat reach gave ~4 m of 'head' (score 100). The
    reach starts on a crest, so a profile that is only carved (running min
    from upstream) would still find 4 m from the crest to the first trough."""
    candidates, config = _wandering_brook(
        tmp_path, channel_rows=ON_LINE,
        dem_fn=lambda x: 20.0 + 2.0 * np.cos(2 * np.pi * x / 150.0))
    assert score_hydro(candidates, config).iloc[0] == 0


def test_profile_rising_downstream_has_no_head(tmp_path):
    """Regression (cell 263): a reach whose DEM profile ends 2 m higher than
    it starts, plus undulation, still got 3+ m of head."""
    candidates, config = _wandering_brook(
        tmp_path, channel_rows=ON_LINE,
        dem_fn=lambda x: 20.0 + 0.002 * x + 1.0 * np.sin(2 * np.pi * x / 150.0))
    assert score_hydro(candidates, config).iloc[0] == 0


def test_head_comes_from_pairs_at_most_500_m_apart(tmp_path):
    """Regression: samples up to 1 km apart (±500 m), both ends possibly
    outside the cell buffer, added two 2 m drops 900 m apart into 4 m."""
    candidates, config = _wandering_brook(
        tmp_path, channel_rows=ON_LINE,
        dem_fn=lambda x: np.where(x < 50, 30.0, np.where(x < 950, 28.0, 26.0)))
    assert score_hydro(candidates, config).iloc[0] == 0


def test_pairs_run_on_into_the_downstream_line(tmp_path):
    """The flow path continues past a segment break: a cell at the end of an
    upper segment reaches the drop on the segment it flows into."""
    candidates, config = _wandering_brook(
        tmp_path, channel_rows=ON_LINE,
        dem_fn=lambda x: np.where(x < 700, 50.0, 50.0 - 0.05 * (x - 700)))
    upper = LineString([(X0, Y0 + 10), (X0 + 500, Y0 + 10)])
    lower = LineString([(X0 + 500, Y0 + 10), (X0 + 1000, Y0 + 10)])
    gpd.GeoDataFrame(
        {"FEAT_CODE": ["WARV50", "WARV50"], "LINE_CLASS": [1, 1], "FLOWDIR": [1, 1]},
        geometry=[upper, lower], crs="EPSG:2961",
    ).to_file(tmp_path / "streams.gpkg", driver="GPKG")
    # cell x in [125, 375] m, so its buffer is x in [25, 475]: all on the upper line
    cell = box(X0 + 125, Y0 - 65, X0 + 375, Y0 + 185)
    candidates = candidates.set_geometry([cell])
    # pair from x=470 (in the buffer) to x=970: 13.5 m of head at 24 km²
    assert score_hydro(candidates, config).iloc[0] == 100


def test_pair_flow_is_taken_at_the_upper_end():
    from src.scoring.hydro import _pair_powers

    z = np.linspace(30.0, 0.0, 31)                 # 1 m per sample, downhill
    area = np.where(np.arange(31) < 15, 0.3, 24.0)  # small above, big below
    valid = np.ones(31, dtype=bool)
    p = _pair_powers(z, valid, area, n_own=31, k=10)
    assert (p[:15] == 0).all()                      # intake under 0.5 km²
    assert p[15] == pytest.approx(_estimate_power(_estimate_flow_rate(24.0), 10.0))
    # head is upstream minus downstream: an uphill pair has none
    assert (_pair_powers(z[::-1], valid, np.full(31, 24.0), 31, 10) == 0).all()


def test_a_rise_far_down_the_flow_path_keeps_the_head_above_it():
    """Regression (cell 6062): the filled profile was a running max over the
    whole downstream path, so a 2 m DEM rise on the next line (a road fill,
    or the DEM off the channel) zeroed every pair above it on the line."""
    from src.scoring.hydro import _pair_powers

    z = 30.0 - 0.05 * np.arange(300)               # 1% grade: 5 m per 500 m
    z[250:256] = 40.0                              # rise 1,250 m downstream
    area = np.full(300, 24.0)
    p = _pair_powers(z, np.ones(300, dtype=bool), area, n_own=300, k=100)
    assert p[0] == pytest.approx(_estimate_power(_estimate_flow_rate(24.0), 5.0))
    # a pair ending within HYDRO_HEAD_CONDITION_M above the rise is still
    # read as ponded behind it
    assert p[120] == 0


# --- flow-accumulation validity on land only (H6) ---------------------------
def _facc_with_sea(tmp_path, routing_name="dem_flowres.tif"):
    """A 20 x 20 accumulation whose land (west half) never got past 50 cells
    but whose sea (east half, DEM 0 m) piled up 50,000."""
    facc = np.full((20, 20), 50.0)
    facc[:, 10:] = 50000.0
    _write_raster(tmp_path / "flow_accumulation.tif", FACC_BOUNDS, facc, nodata=-32768)
    dem = np.full((20, 20), 30.0)
    dem[:, 10:] = 0.0
    _write_raster(tmp_path / routing_name, FACC_BOUNDS, dem, nodata=-32767)
    return tmp_path / "flow_accumulation.tif"


def test_flow_accumulation_validity_ignores_sea_cells(tmp_path):
    """Regression: the check took the max over every cell, and an old raster
    routed with the sea in it passes on sea cells alone."""
    from src.scoring.hydro import flow_accumulation_valid

    assert not flow_accumulation_valid(_facc_with_sea(tmp_path))


def test_flow_accumulation_validity_without_a_routing_dem_on_its_grid(tmp_path):
    """With no DEM on the accumulation's grid there is nothing to mask the
    sea with, so every cell counts (as before)."""
    from src.scoring.hydro import flow_accumulation_valid

    path = _facc_with_sea(tmp_path, routing_name="other.tif")
    _write_raster(tmp_path / "dem.tif", FACC_BOUNDS, np.full((40, 40), 0.0), nodata=-32767)
    assert flow_accumulation_valid(path)

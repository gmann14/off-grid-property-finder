"""Smoke tests for run_visualize (705 lines, previously zero test coverage).

Not pixel-perfect map assertions — just "does it run without crashing and
produce a map.html containing the expected layers" for the layer-drift class
of bug the review flagged (keep_cols silently rotting after a scoring change).
"""

import logging

import geopandas as gpd
from shapely.geometry import LineString, box

from src.config import Config, Paths, StudyArea
from src.visualize import run_visualize

CRS = "EPSG:2961"
BBOX = (380000.0, 4900000.0, 381000.0, 4901000.0)


def _scored_cells():
    """A handful of cells spanning every score band + one excluded cell."""
    xmin, ymin, xmax, ymax = BBOX
    step = (xmax - xmin) / 6
    scores = [95.0, 70.0, 50.0, 30.0, 10.0, None]  # excellent..unsuitable, excluded
    geoms = [box(xmin + i * step, ymin, xmin + (i + 1) * step, ymax) for i in range(6)]
    return gpd.GeoDataFrame(
        {
            "status": ["eligible"] * 5 + ["excluded"],
            "score": scores,
            "score_allrounder": [s if s is not None else None for s in scores],
            "rank": [1, 2, 3, 4, 5, None],
            "score_hydro": scores,
            "score_access": scores,
            "score_open_ground": scores,
            "score_wind": scores,
            "score_elevation": scores,
            "confidence": [90.0, 80.0, 70.0, 60.0, 50.0, None],
            "confidence_band": ["high", "high", "medium", "medium", "low", None],
        },
        geometry=geoms,
        crs=CRS,
    )


def _cfg(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    return Config(
        study_area=StudyArea(bbox=BBOX, name="smoke"),
        paths=Paths(raw=tmp_path / "raw", processed=tmp_path / "processed", output=output),
    ), output


def test_run_visualize_minimal_produces_map(tmp_path):
    """Only scored_cells.gpkg present — every overlay layer is optional."""
    cfg, output = _cfg(tmp_path)
    _scored_cells().to_file(output / "scored_cells.gpkg", driver="GPKG")

    run_visualize(cfg, logging.getLogger("test"))

    map_path = output / "map.html"
    assert map_path.exists()
    html = map_path.read_text()
    assert len(html) > 1000  # not an empty/stub file

    # Score band layers must be present (this is exactly the kind of thing
    # that silently rotted before — band names hardcoded in visualize.py).
    for band in ["Excellent (80-100)", "Good (60-79)", "Fair (40-59)",
                 "Poor (20-39)", "Unsuitable (0-19)", "Excluded"]:
        assert band in html


def test_run_visualize_missing_scored_cells_does_not_crash(tmp_path, caplog):
    cfg, output = _cfg(tmp_path)
    with caplog.at_level(logging.ERROR):
        run_visualize(cfg, logging.getLogger("test"))
    assert not (output / "map.html").exists()
    assert any("Scored cells not found" in r.message for r in caplog.records)


def test_run_visualize_includes_optional_layers_when_present(tmp_path):
    cfg, output = _cfg(tmp_path)
    processed = cfg.paths.processed
    processed.mkdir(parents=True)
    _scored_cells().to_file(output / "scored_cells.gpkg", driver="GPKG")

    xmin, ymin, xmax, ymax = BBOX
    gpd.GeoDataFrame(
        {"LINE_CLASS": [1]}, geometry=[LineString([(xmin, ymin), (xmax, ymax)])], crs=CRS,
    ).to_file(processed / "streams.gpkg", driver="GPKG")
    gpd.GeoDataFrame(
        {"highway": ["primary"]}, geometry=[LineString([(xmin, ymin), (xmax, ymin)])], crs=CRS,
    ).to_file(processed / "roads.gpkg", driver="GPKG")

    # Stage-B parcel layer (a separate optional overlay read from output/)
    gpd.GeoDataFrame(
        {"PID": ["60010001"], "score": [88.0], "rank": [1]},
        geometry=[box(xmin, ymin, xmax, ymax)], crs=CRS,
    ).to_file(output / "scored_parcels.gpkg", driver="GPKG")

    run_visualize(cfg, logging.getLogger("test"))

    html = (output / "map.html").read_text()
    assert "Streams &amp; Rivers" in html or "Streams & Rivers" in html
    assert "Roads" in html
    assert "Scored Parcels (PID)" in html

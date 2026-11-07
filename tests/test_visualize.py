"""Smoke tests for run_visualize (705 lines, previously zero test coverage).

Not pixel-perfect map assertions — just "does it run without crashing and
produce a map.html containing the expected layers" for the layer-drift class
of bug the review flagged (keep_cols silently rotting after a scoring change).
"""

import logging

import geopandas as gpd
from shapely.geometry import LineString, box

from src.config import Config, Paths, StudyArea
from src.visualize import _add_parcels, run_visualize

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
    # Assert on the layer-control entries, not the raw text: "Excluded" is
    # also in the legend, which let a never-drawn excluded layer pass before.
    import re
    from src.visualize import EXCLUDED_LAYER_NAME
    for band in ["Excellent (80-100)", "Good (60-79)", "Fair (40-59)",
                 "Poor (20-39)", "Unsuitable (0-19)", EXCLUDED_LAYER_NAME]:
        assert re.search(re.escape(f'"{band}"') + r"\s*:\s*geo_json_", html), band


def test_run_visualize_missing_scored_cells_does_not_crash(tmp_path, caplog):
    cfg, output = _cfg(tmp_path)
    with caplog.at_level(logging.ERROR):
        assert run_visualize(cfg, logging.getLogger("test")) is False
    assert not (output / "map.html").exists()
    assert any("Scored cells not found" in r.message for r in caplog.records)


def test_run_visualize_refuses_stale_processed_data(tmp_path):
    import json

    import pytest

    from src.manifest import MANIFEST_FILENAME, StaleProcessedDataError

    cfg, output = _cfg(tmp_path)
    _scored_cells().to_file(output / "scored_cells.gpkg", driver="GPKG")
    cfg.paths.processed.mkdir()
    (cfg.paths.processed / MANIFEST_FILENAME).write_text(json.dumps(
        {"study_area_bbox": [0, 0, 1, 1], "cell_size_m": 250, "working_crs": cfg.working_crs}))
    with pytest.raises(StaleProcessedDataError):
        run_visualize(cfg, logging.getLogger("test"))
    assert not (output / "map.html").exists()


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


def test_add_parcels_drops_unscored_rows(tmp_path):
    """Regression for the map-size fix: on the real study area, unscored
    parcels (no candidate cells assigned) outnumbered scored ones ~4:1 and
    rendered near-invisible — pure payload bloat with zero information value.
    Only scored parcels should reach the map."""
    import folium

    xmin, ymin, xmax, ymax = BBOX
    step = (xmax - xmin) / 4
    parcels_path = tmp_path / "scored_parcels.gpkg"
    gpd.GeoDataFrame(
        {
            "PID": ["60010001", "60010002", "60010003", "60010004"],
            "score": [88.0, None, 72.0, None],  # 2 scored, 2 unscored
        },
        geometry=[box(xmin + i * step, ymin, xmin + (i + 1) * step, ymax) for i in range(4)],
        crs=CRS,
    ).to_file(parcels_path, driver="GPKG")

    m = folium.Map(location=[44.4, -64.5], zoom_start=11)
    _add_parcels(m, parcels_path)
    folium.LayerControl().add_to(m)
    html = m.get_root().render()

    assert "60010001" in html
    assert "60010003" in html
    assert "60010002" not in html
    assert "60010004" not in html


def test_add_parcels_no_score_column_includes_all(tmp_path):
    """If a caller passes a parcels file with no 'score' column at all (not
    the normal path, but shouldn't crash), nothing should be filtered."""
    import folium

    parcels_path = tmp_path / "parcels_no_score.gpkg"
    gpd.GeoDataFrame(
        {"PID": ["60010001"]}, geometry=[box(*BBOX)], crs=CRS,
    ).to_file(parcels_path, driver="GPKG")

    m = folium.Map(location=[44.4, -64.5], zoom_start=11)
    _add_parcels(m, parcels_path)  # must not raise
    folium.LayerControl().add_to(m)
    assert "60010001" in m.get_root().render()


def test_formula_panel_shows_the_configured_weights_and_parcel_formula(tmp_path):
    from src.visualize import _formula_panel_html

    cfg, _ = _cfg(tmp_path)
    cfg.weights = {"hydro": 50, "access": 30, "open_ground": 20, "wind": 0, "elevation": 0}
    cfg.preferences = {"parcel_aggregation": {"top_n": 5, "cell_weight": 0.8}}
    cfg.min_parcel_area_acres = 2.5
    html = _formula_panel_html(cfg)
    assert ">50%<" in html and ">30%<" in html and ">20%<" in html
    assert "Wind" not in html and "Elevation" not in html     # weight 0: omitted
    assert "0.80 &times; mean(top 5 cells) + 0.20 &times;" in html
    assert "under 2.5 acres of land" in html


def test_formula_panel_defaults_match_config_yaml():
    from src.config import load_config
    from src.visualize import _formula_panel_html

    html = _formula_panel_html(load_config("config.yaml"))
    for pct in (">40%<", ">20%<", ">15%<", ">10%<"):
        assert pct in html
    assert "0.65 &times; mean(top 3 cells) + 0.35 &times;" in html
    assert "under 1 acre of land" in html


def test_excluded_cells_are_drawn_dissolved_by_sorted_reason(tmp_path):
    """Excluded cells read back from GPKG have score NaN (not None); they must
    still reach the map, grouped by reason regardless of the reasons' order."""
    import json
    import re

    cfg, output = _cfg(tmp_path)
    cells = _scored_cells()
    extra = cells.iloc[[5, 5]].copy()
    extra.geometry = [box(380000, 4901000, 380100, 4901100), box(380100, 4901000, 380200, 4901100)]
    cells = gpd.GeoDataFrame(
        __import__("pandas").concat([cells, extra], ignore_index=True), crs=CRS)
    cells["exclusion_reasons"] = [None] * 5 + ["flood_zone; water", "water; flood_zone", "protected_area"]
    cells.to_file(output / "scored_cells.gpkg", driver="GPKG")

    assert run_visualize(cfg, logging.getLogger("test")) is True
    html = (output / "map.html").read_text()
    blobs = re.findall(r'geo_json_[0-9a-f]+_add\((\{.*?\})\);', html, re.S)
    reasons = sorted(
        f["properties"]["reasons"] for b in blobs for f in json.loads(b)["features"]
        if "reasons" in f["properties"])
    assert reasons == ["flood_zone; water", "protected_area"]


def test_score_color_treats_nan_as_no_score():
    from src.visualize import _score_color, _score_opacity

    assert _score_color(float("nan")) == _score_color(None) == "#808080"
    assert _score_opacity(float("nan")) == _score_opacity(None)

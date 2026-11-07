"""Vector-layer ingest steps: civic addresses, file discovery, atomic writes."""

import geopandas as gpd
import pytest
from shapely.geometry import Point

import src.ingest as ingest
from src.config import Config, Paths, StudyArea
from src.ingest import ingest_civic, run_ingest

CRS = "EPSG:2961"
BBOX = (380000.0, 4900000.0, 381000.0, 4901000.0)
XMIN, YMIN, XMAX, YMAX = BBOX


def _cfg(tmp_path):
    return Config(
        study_area=StudyArea(bbox=BBOX, name="t"),
        paths=Paths(raw=tmp_path / "raw", processed=tmp_path / "processed", output=tmp_path / "out"),
    )


def _points(coords, crs=CRS, **cols):
    return gpd.GeoDataFrame(cols, geometry=[Point(x, y) for x, y in coords], crs=crs)


# --- civic addresses -------------------------------------------------------

def test_ingest_civic_reads_reprojects_and_clips(tmp_path):
    cfg = _cfg(tmp_path)
    civic_dir = cfg.paths.raw / "civic"
    civic_dir.mkdir(parents=True)
    # one file per county, as the NS civic address file is often downloaded
    _points([(XMIN + 100, YMIN + 100), (XMAX + 500, YMIN + 100)], civic_no=[1, 2]).to_file(
        civic_dir / "lunenburg.gpkg", driver="GPKG")
    _points([(XMIN + 900, YMIN + 900)], civic_no=[3]).to_crs("EPSG:4326").to_file(
        civic_dir / "queens.geojson", driver="GeoJSON")

    out = ingest_civic(cfg)
    assert out == cfg.paths.processed / "civic.gpkg"
    result = gpd.read_file(out)
    assert result.crs.to_epsg() == 2961
    assert sorted(result["civic_no"]) == [1, 3]   # the point outside the bbox is dropped
    assert result.geometry.x.min() >= XMIN and result.geometry.x.max() <= XMAX


def test_ingest_civic_missing_returns_none(tmp_path):
    cfg = _cfg(tmp_path)
    assert ingest_civic(cfg) is None
    assert not (cfg.paths.processed / "civic.gpkg").exists()


def test_run_ingest_runs_the_civic_step(tmp_path, monkeypatch):
    """run_ingest must ingest civic addresses: scoring/access.py reads
    processed/civic.gpkg (a civic address in a cell = access 100)."""
    cfg = _cfg(tmp_path)
    civic_dir = cfg.paths.raw / "civic"
    civic_dir.mkdir(parents=True)
    _points([(XMIN + 100, YMIN + 100)], civic_no=[1]).to_file(civic_dir / "civic.gpkg", driver="GPKG")
    # Every other step is stubbed out (flood and wind would hit the network).
    for name in dir(ingest):
        if name.startswith("ingest_") and name != "ingest_civic":
            monkeypatch.setattr(ingest, name, lambda config, *a, **k: None)

    results = run_ingest(cfg, ingest.logger)
    assert results["civic"] == cfg.paths.processed / "civic.gpkg"
    assert results["civic"].exists()

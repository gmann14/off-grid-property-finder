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


def _fail_writes_after_creating_file(monkeypatch):
    """Make GeoDataFrame.to_file create its target and then fail, as a full
    disk or a killed process does part-way through a write."""
    def to_file(self, filename, *args, **kwargs):
        with open(filename, "wb") as fh:
            fh.write(b"SQLite format 3\x00 truncated")
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(gpd.GeoDataFrame, "to_file", to_file)


def test_failed_vector_write_is_not_cached(tmp_path, monkeypatch):
    """A write that fails part-way must not leave the output in place: every
    step skips when its output exists, so a partial file would be used forever."""
    cfg = _cfg(tmp_path)
    civic_dir = cfg.paths.raw / "civic"
    civic_dir.mkdir(parents=True)
    _points([(XMIN + 100, YMIN + 100)], civic_no=[1]).to_file(civic_dir / "civic.gpkg", driver="GPKG")

    with monkeypatch.context() as m:
        _fail_writes_after_creating_file(m)
        with pytest.raises(OSError):
            ingest_civic(cfg)
    assert not (cfg.paths.processed / "civic.gpkg").exists()
    assert not list(cfg.paths.processed.glob("*partial*"))

    out = ingest_civic(cfg)   # the next run rebuilds instead of skipping
    assert len(gpd.read_file(out)) == 1
    assert gpd.read_file(out, layer="civic").shape[0] == 1   # layer keeps its name


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


# --- several raw files per layer ---------------------------------------------

def _polys(path, rects, driver=None, layer=None, mode="w", **cols):
    """Write box polygons to ``path`` (format from the extension, or ``driver``)."""
    from shapely.geometry import box

    path.parent.mkdir(parents=True, exist_ok=True)
    gdf = gpd.GeoDataFrame(cols, geometry=[box(*r) for r in rects], crs=CRS)
    kwargs = {"layer": layer, "mode": mode} if layer else {}
    if driver:
        kwargs["driver"] = driver
    gdf.to_file(path, **kwargs)
    return path


def _r(i):
    """The i-th 100 m box along the bbox's south edge."""
    return (XMIN + 100 * i, YMIN, XMIN + 100 * i + 90, YMIN + 90)


def test_protected_areas_read_every_file_in_their_subdirectory(tmp_path):
    cfg = _cfg(tmp_path)
    pa = cfg.paths.raw / "exclusions" / "protected_areas"
    _polys(pa / "provincial.shp", [_r(0)], name=["Prov"])
    _polys(pa / "nature_reserves.shp", [_r(1)], name=["NR"])
    # a flood layer elsewhere under exclusions/ must not become "protected areas"
    _polys(cfg.paths.raw / "exclusions" / "flood" / "flood_2100.shp", [_r(5)], name=["Flood"])

    out = ingest.ingest_protected_areas(cfg)
    assert sorted(gpd.read_file(out)["name"]) == ["NR", "Prov"]


def test_protected_areas_single_file_anywhere_under_exclusions(tmp_path):
    cfg = _cfg(tmp_path)
    _polys(cfg.paths.raw / "exclusions" / "export" / "pa.shp", [_r(0)], name=["Prov"])
    out = ingest.ingest_protected_areas(cfg)
    assert list(gpd.read_file(out)["name"]) == ["Prov"]


def test_protected_areas_ambiguous_files_fail_clearly(tmp_path):
    """Two shapefiles loose under exclusions/ (say protected areas and a flood
    layer): which one is protected areas can't be told, so ingest refuses
    rather than tagging an arbitrary one as protected."""
    from src.raw_layers import AmbiguousRawDataError

    cfg = _cfg(tmp_path)
    _polys(cfg.paths.raw / "exclusions" / "a_flood.shp", [_r(5)], name=["Flood"])
    _polys(cfg.paths.raw / "exclusions" / "b_protected.shp", [_r(0)], name=["Prov"])
    with pytest.raises(AmbiguousRawDataError, match="protected_areas"):
        ingest.ingest_protected_areas(cfg)
    assert not (cfg.paths.processed / "protected_areas.gpkg").exists()


def test_waterbodies_read_every_file(tmp_path):
    cfg = _cfg(tmp_path)
    _polys(cfg.paths.raw / "water" / "lakes.gpkg", [_r(0)], name=["lake"])
    _polys(cfg.paths.raw / "water" / "coast" / "ocean.shp", [_r(1)], name=["ocean"])
    _polys(cfg.paths.raw / "waterbodies" / "rivers.shp", [_r(2)], name=["river"])
    out = ingest.ingest_waterbodies(cfg)
    assert sorted(gpd.read_file(out)["name"]) == ["lake", "ocean", "river"]


def test_buildings_read_every_tile_and_drop_cross_file_duplicates(tmp_path):
    cfg = _cfg(tmp_path)
    b = cfg.paths.raw / "buildings"
    _polys(b / "tile_a.gpkg", [_r(0), _r(1)], bid=[1, 2])
    _polys(b / "tile_b.gpkg", [_r(2)], bid=[3])
    _polys(b / "copy_of_a.gpkg", [_r(0), _r(1)], bid=[1, 2])   # same footprints again
    out = ingest.ingest_buildings(cfg)
    assert sorted(gpd.read_file(out)["bid"]) == [1, 2, 3]


def test_parcels_read_every_file(tmp_path):
    """The study area spans two counties; parcels downloaded per county."""
    cfg = _cfg(tmp_path)
    p = cfg.paths.raw / "parcels"
    _polys(p / "lunenburg.gpkg", [_r(0), _r(1)], PID=["60000001", "60000002"])
    _polys(p / "queens.shp", [_r(2)], PID=["70000001"])
    out = ingest.ingest_parcels(cfg, source="local")
    assert sorted(gpd.read_file(out)["PID"]) == ["60000001", "60000002", "70000001"]


def test_parcels_geodatabase_reads_its_parcel_layer(tmp_path):
    cfg = _cfg(tmp_path)
    gdb = cfg.paths.raw / "parcels" / "NSPRD.gdb"
    _polys(gdb, [_r(0)], driver="OpenFileGDB", layer="Easements", PID=["99999999"])
    _polys(gdb, [_r(1), _r(2)], driver="OpenFileGDB", layer="NSPRD_Parcels", mode="a",
           PID=["60000001", "60000002"])
    out = ingest.ingest_parcels(cfg, source="local")
    assert sorted(gpd.read_file(out)["PID"]) == ["60000001", "60000002"]


def test_parcels_geodatabase_with_unclear_layers_fails_clearly(tmp_path):
    from src.raw_layers import AmbiguousRawDataError

    cfg = _cfg(tmp_path)
    gdb = cfg.paths.raw / "parcels" / "data.gdb"
    _polys(gdb, [_r(0)], driver="OpenFileGDB", layer="layer_a", PID=["60000001"])
    _polys(gdb, [_r(1)], driver="OpenFileGDB", layer="layer_b", mode="a", PID=["60000002"])
    with pytest.raises(AmbiguousRawDataError, match="layer_a"):
        ingest.ingest_parcels(cfg, source="local")


@pytest.mark.parametrize("step,subdir,names", [
    ("ingest_crown_land", "crown-land", ("crown_a.shp", "crown_b.shp")),
    ("ingest_streams", "hydro", ("nshn_lines.shp", "other.shp")),
])
def test_single_file_layers_fail_clearly_when_several_files(tmp_path, step, subdir, names):
    from src.raw_layers import AmbiguousRawDataError

    cfg = _cfg(tmp_path)
    for i, name in enumerate(names):
        _polys(cfg.paths.raw / subdir / name, [_r(i)], n=[i])
    with pytest.raises(AmbiguousRawDataError, match=names[0]):
        getattr(ingest, step)(cfg)


def test_check_data_reports_ambiguous_layer(tmp_path):
    import logging

    from src.check_data import run_check_data

    cfg = _cfg(tmp_path)
    _polys(cfg.paths.raw / "exclusions" / "a_flood.shp", [_r(5)], name=["Flood"])
    _polys(cfg.paths.raw / "exclusions" / "b_protected.shp", [_r(0)], name=["Prov"])
    results = run_check_data(cfg, logging.getLogger("test"))
    assert results["protected_areas"]["status"].startswith("AMBIGUOUS")

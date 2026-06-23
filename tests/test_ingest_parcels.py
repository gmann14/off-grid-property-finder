"""Tests for parcel ingestion, PID/AAN normalization, and NSPRD REST helpers."""

import geopandas as gpd
from shapely.geometry import box

from src.config import Config, Paths, StudyArea
from src.ingest import (
    _build_nsprd_query_url,
    _features_to_gdf,
    _normalize_parcel_fields,
    ingest_parcels,
)

CRS = "EPSG:2961"
BBOX = (380000.0, 4900000.0, 381000.0, 4901000.0)


def _write_raw_parcels(raw_dir, columns, leading_zero_pid="60010001"):
    """Write a raw parcels GPKG with the given id-column names into raw/parcels/."""
    parcels_dir = raw_dir / "parcels"
    parcels_dir.mkdir(parents=True, exist_ok=True)
    xmin, ymin, xmax, ymax = BBOX
    data = {columns["pid"]: [leading_zero_pid, "60010002"]}
    if "aan" in columns:
        data[columns["aan"]] = ["01000001", "01000002"]
    gdf = gpd.GeoDataFrame(
        data,
        geometry=[box(xmin, ymin, xmax, (ymin + ymax) / 2),
                  box(xmin, (ymin + ymax) / 2, xmax, ymax)],
        crs=CRS,
    )
    path = parcels_dir / "parcels.gpkg"
    gdf.to_file(path, driver="GPKG")
    return path


def _config(tmp_path):
    return Config(
        study_area=StudyArea(bbox=BBOX, name="test"),
        paths=Paths(
            raw=tmp_path / "raw",
            processed=tmp_path / "processed",
            output=tmp_path / "output",
        ),
    )


# --- field normalization ---------------------------------------------------

def test_normalize_renames_pid_variants():
    gdf = gpd.GeoDataFrame(
        {"PID_NUMBER": [60010001], "ASSESS_NO": [1000]},
        geometry=[box(0, 0, 1, 1)], crs=CRS,
    )
    out = _normalize_parcel_fields(gdf)
    assert "PID" in out.columns
    assert "AAN" in out.columns
    # Numeric source coerced to clean string (no trailing ".0")
    assert out["PID"].iloc[0] == "60010001"


def test_normalize_missing_pid_blank_column():
    gdf = gpd.GeoDataFrame({"foo": [1]}, geometry=[box(0, 0, 1, 1)], crs=CRS)
    out = _normalize_parcel_fields(gdf)
    assert "PID" in out.columns
    assert out["PID"].iloc[0] == ""


def test_normalize_preserves_leading_zero_string_pid():
    gdf = gpd.GeoDataFrame(
        {"PID": ["00017183"]}, geometry=[box(0, 0, 1, 1)], crs=CRS,
    )
    out = _normalize_parcel_fields(gdf)
    assert out["PID"].iloc[0] == "00017183"


# --- REST helpers (pure, no network) ---------------------------------------

def test_build_query_url_has_bbox_and_pagination():
    url = _build_nsprd_query_url(
        "https://host/svc/MapServer", 0, (-64.7, 44.1, -64.1, 44.6), 1000, 500
    )
    assert "/0/query?" in url
    assert "geometryType=esriGeometryEnvelope" in url
    assert "resultOffset=1000" in url
    assert "resultRecordCount=500" in url
    assert "f=geojson" in url


def test_features_to_gdf_parses_geojson():
    fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"PID": "60010001"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[-64.7, 44.1], [-64.6, 44.1],
                                     [-64.6, 44.2], [-64.7, 44.1]]],
                },
            }
        ],
    }
    gdf = _features_to_gdf(fc)
    assert len(gdf) == 1
    assert gdf["PID"].iloc[0] == "60010001"
    assert str(gdf.crs).upper().endswith("4326")


def test_features_to_gdf_empty():
    gdf = _features_to_gdf({"features": []})
    assert gdf.empty


# --- end-to-end local ingestion --------------------------------------------

def test_ingest_parcels_local(tmp_path):
    cfg = _config(tmp_path)
    _write_raw_parcels(cfg.paths.raw, {"pid": "PID_NUMBER", "aan": "ASSESS_NO"})

    out = ingest_parcels(cfg, source="local")
    assert out is not None and out.exists()

    result = gpd.read_file(out)
    assert "PID" in result.columns
    assert "AAN" in result.columns
    assert "area_acres" in result.columns
    assert (result["area_acres"] > 0).all()
    assert str(result.crs).upper().endswith("2961")


def test_ingest_parcels_missing_returns_none(tmp_path):
    cfg = _config(tmp_path)
    (cfg.paths.raw / "parcels").mkdir(parents=True, exist_ok=True)
    assert ingest_parcels(cfg, source="local") is None

"""Tests for parcel ingestion, PID/AAN normalization, and NSPRD REST helpers."""

import geopandas as gpd
import pytest
from shapely.geometry import box

from src.config import Config, Paths, StudyArea
from unittest.mock import MagicMock, patch

from src.ingest import (
    _build_nsprd_query_url,
    _dissolve_by_pid,
    _features_to_gdf,
    _normalize_parcel_fields,
    fetch_nsprd_parcels,
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


def test_normalize_zfills_numerically_loaded_pid():
    # A driver that loaded PID as an int dropped the leading zeros; re-pad to 8.
    gdf = gpd.GeoDataFrame(
        {"PID": [17183, 60010001]}, geometry=[box(0, 0, 1, 1), box(1, 0, 2, 1)], crs=CRS,
    )
    out = _normalize_parcel_fields(gdf)
    assert out["PID"].iloc[0] == "00017183"   # 5-digit int -> zero-padded
    assert out["PID"].iloc[1] == "60010001"   # already 8 digits, unchanged


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


# --- ArcGIS error-JSON + PID-coverage guards (schema-drift protection) ----

def _mock_response(json_body, status_ok=True):
    resp = MagicMock()
    resp.json.return_value = json_body
    resp.raise_for_status = MagicMock() if status_ok else MagicMock(side_effect=Exception("http error"))
    return resp


def test_fetch_nsprd_aborts_on_error_json_mid_pagination():
    """A 200-with-error-body mid-pagination must abort the whole fetch, not be
    treated as end-of-data (which would silently return a truncated result)."""
    good_page = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "properties": {"PID": "60010001"},
                      "geometry": {"type": "Polygon", "coordinates": [[[-64.7, 44.1], [-64.6, 44.1],
                                                                        [-64.6, 44.2], [-64.7, 44.1]]]}}],
    }
    error_page = {"error": {"code": 429, "message": "Request throttled"}}
    with patch("requests.get") as mock_get:
        mock_get.side_effect = [_mock_response(good_page), _mock_response(error_page)]
        result = fetch_nsprd_parcels((360000, 4880000, 410000, 4930000), page_size=1)
    assert result is None  # aborted, not a partial 1-parcel result


def test_fetch_nsprd_empty_first_page_returns_none():
    with patch("requests.get") as mock_get:
        mock_get.return_value = _mock_response({"type": "FeatureCollection", "features": []})
        result = fetch_nsprd_parcels((360000, 4880000, 410000, 4930000))
    assert result is None


def test_ingest_parcels_aborts_on_low_pid_coverage(tmp_path):
    """Simulates schema drift: no column in PARCEL_PID_FIELDS matches, so every
    PID comes back blank. Must abort rather than write an unusable PID list."""
    cfg = _config(tmp_path)
    parcels_dir = cfg.paths.raw / "parcels"
    parcels_dir.mkdir(parents=True)
    xmin, ymin, xmax, ymax = BBOX
    gdf = gpd.GeoDataFrame(
        {"SOME_UNRELATED_FIELD": ["a", "b"]},  # no PID-like column at all
        geometry=[box(xmin, ymin, xmax, (ymin + ymax) / 2),
                  box(xmin, (ymin + ymax) / 2, xmax, ymax)],
        crs=CRS,
    )
    gdf.to_file(parcels_dir / "parcels.gpkg", driver="GPKG")

    assert ingest_parcels(cfg, source="local") is None
    assert not (cfg.paths.processed / "parcels.gpkg").exists()


# --- PID dissolve (fixes duplicate-PID fragments in ranked output) --------

def test_dissolve_merges_duplicate_pid_fragments():
    gdf = gpd.GeoDataFrame(
        {"PID": ["60010001", "60010001", "60010002"]},
        geometry=[box(0, 0, 1, 1), box(1, 0, 2, 1), box(5, 5, 6, 6)],
        crs=CRS,
    )
    result = _dissolve_by_pid(gdf)
    assert len(result) == 2
    assert sorted(result["PID"]) == ["60010001", "60010002"]
    # the two fragments merge into one 2x1 geometry -> area 2.0
    merged_area = result.loc[result.PID == "60010001", "geometry"].area.iloc[0]
    assert merged_area == 2.0


def test_dissolve_leaves_blank_pid_rows_separate():
    """Rows with no PID must NOT be merged into one blob with each other."""
    gdf = gpd.GeoDataFrame(
        {"PID": ["", "", "60010001"]},
        geometry=[box(0, 0, 1, 1), box(10, 10, 11, 11), box(5, 5, 6, 6)],
        crs=CRS,
    )
    result = _dissolve_by_pid(gdf)
    assert len(result) == 3  # both blank-PID rows survive untouched
    assert (result["PID"] == "").sum() == 2


def test_dissolve_noop_when_no_duplicates():
    gdf = gpd.GeoDataFrame(
        {"PID": ["60010001", "60010002"]},
        geometry=[box(0, 0, 1, 1), box(5, 5, 6, 6)],
        crs=CRS,
    )
    result = _dissolve_by_pid(gdf)
    assert len(result) == 2


def test_ingest_parcels_dissolves_fragmented_pid(tmp_path):
    """End-to-end: a parcel split into two fragments by NSPRD/clipping ends up
    as ONE row in the processed output, with combined (not fragment) acreage."""
    cfg = _config(tmp_path)
    parcels_dir = cfg.paths.raw / "parcels"
    parcels_dir.mkdir(parents=True)
    xmin, ymin, xmax, ymax = BBOX
    mid_x = (xmin + xmax) / 2
    gdf = gpd.GeoDataFrame(
        # same PID appears twice (e.g. multi-polygon record split by the source)
        {"PID": ["60010001", "60010001"]},
        geometry=[box(xmin, ymin, mid_x, ymax), box(mid_x, ymin, xmax, ymax)],
        crs=CRS,
    )
    gdf.to_file(parcels_dir / "parcels.gpkg", driver="GPKG")

    out = ingest_parcels(cfg, source="local")
    result = gpd.read_file(out)
    assert len(result) == 1
    assert result["PID"].iloc[0] == "60010001"
    # combined acreage should reflect the full (undivided) bbox, not one half
    full_acres = box(*BBOX).area / 4046.86
    assert result["area_acres"].iloc[0] == pytest.approx(full_acres, rel=1e-6)


def test_ingest_parcels_honors_config_working_crs(tmp_path):
    """Regression: ingest previously reprojected to the WORKING_CRS constant
    regardless of config.working_crs. A raw file in a third CRS should land in
    whatever config.working_crs says, not the hardcoded default."""
    raw = tmp_path / "raw"
    parcels_dir = raw / "parcels"
    parcels_dir.mkdir(parents=True)
    xmin, ymin, xmax, ymax = BBOX
    gdf = gpd.GeoDataFrame(
        {"PID": ["60010001", "60010002"]},
        geometry=[box(xmin, ymin, xmax, (ymin + ymax) / 2),
                  box(xmin, (ymin + ymax) / 2, xmax, ymax)],
        crs=CRS,
    ).to_crs("EPSG:4326")  # store raw file in a different CRS than the target
    gdf.to_file(parcels_dir / "parcels.gpkg", driver="GPKG")

    custom_crs = "EPSG:3857"  # deliberately NOT the WORKING_CRS constant (2961)
    cfg = Config(
        study_area=StudyArea(bbox=BBOX, name="test"),
        working_crs=custom_crs,
        paths=Paths(raw=raw, processed=tmp_path / "processed", output=tmp_path / "output"),
    )
    # Clip bbox must match the custom CRS too, since ingest_parcels clips in
    # config.working_crs terms via the reprojected geometry.
    import pyproj
    from shapely.ops import transform as shp_transform
    transformer = pyproj.Transformer.from_crs(CRS, custom_crs, always_xy=True).transform
    reproj_bounds = shp_transform(transformer, box(*BBOX)).bounds
    cfg.study_area.bbox = reproj_bounds

    out = ingest_parcels(cfg, source="local")
    assert out is not None
    result = gpd.read_file(out)
    assert result.crs.to_epsg() == 3857


def test_ingest_parcels_missing_returns_none(tmp_path):
    cfg = _config(tmp_path)
    (cfg.paths.raw / "parcels").mkdir(parents=True, exist_ok=True)
    assert ingest_parcels(cfg, source="local") is None

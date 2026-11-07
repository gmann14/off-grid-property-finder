"""Tests for exclusion layer loading and application."""

import geopandas as gpd
import pytest
from shapely.geometry import Polygon, box

from src.exclusions import (
    apply_exclusions, load_exclusions, parcel_assignment_points, parcel_land_acres,
)

CRS = "EPSG:2961"
BBOX = (380000.0, 4900000.0, 381000.0, 4901000.0)


def test_load_exclusions_no_files():
    result = load_exclusions(None, None, CRS, BBOX)
    assert result.empty
    assert "exclusion_reason" in result.columns


def test_load_exclusions_with_protected(sample_exclusion_zones):
    result = load_exclusions(sample_exclusion_zones, None, CRS, BBOX)
    assert len(result) > 0
    assert (result["exclusion_reason"] == "protected_area").all()


def test_apply_exclusions_empty():
    grid = gpd.GeoDataFrame(
        geometry=[box(0, 0, 250, 250)],
        crs=CRS,
    )
    empty = gpd.GeoDataFrame(columns=["geometry", "exclusion_reason"], crs=CRS)
    result = apply_exclusions(grid, empty)
    assert (result["status"] == "eligible").all()


def test_apply_exclusions_centroid_containment():
    xmin, ymin = BBOX[0], BBOX[1]
    # Cell whose centroid is inside the exclusion zone
    grid = gpd.GeoDataFrame(
        geometry=[box(xmin, ymin, xmin + 250, ymin + 250)],
        crs=CRS,
    )
    exclusions = gpd.GeoDataFrame(
        {"exclusion_reason": ["protected_area"]},
        geometry=[box(xmin, ymin, xmin + 500, ymin + 500)],
        crs=CRS,
    )
    result = apply_exclusions(grid, exclusions)
    assert result["status"].iloc[0] == "excluded"
    assert "protected_area" in result["exclusion_reasons"].iloc[0]


def test_apply_exclusions_overlap_threshold():
    xmin, ymin = BBOX[0], BBOX[1]
    # Cell: 250x250, exclusion covers 60% of it
    grid = gpd.GeoDataFrame(
        geometry=[box(xmin, ymin, xmin + 250, ymin + 250)],
        crs=CRS,
    )
    # Exclusion doesn't contain centroid but overlaps >50%
    exclusions = gpd.GeoDataFrame(
        {"exclusion_reason": ["flood_zone"]},
        geometry=[box(xmin - 100, ymin - 100, xmin + 200, ymin + 200)],
        crs=CRS,
    )
    result = apply_exclusions(grid, exclusions, overlap_threshold=0.5)
    # Overlap = 200*200 = 40000, Cell = 250*250 = 62500, ratio = 0.64 > 0.5
    assert result["status"].iloc[0] == "excluded"
    assert "flood_zone" in result["exclusion_reasons"].iloc[0]


def test_apply_exclusions_overlap_below_threshold_stays_eligible():
    """Regression for the spatial-join rewrite: cells that touch an exclusion
    polygon but don't clear the overlap threshold must stay eligible — the
    sjoin pre-filter must not itself decide exclusion, only narrow candidates."""
    xmin, ymin = BBOX[0], BBOX[1]
    grid = gpd.GeoDataFrame(geometry=[box(xmin, ymin, xmin + 250, ymin + 250)], crs=CRS)
    # Small corner overlap only: 20x20 = 400 out of 62500 -> ~0.6%, well under 50%
    exclusions = gpd.GeoDataFrame(
        {"exclusion_reason": ["flood_zone"]},
        geometry=[box(xmin - 10, ymin - 10, xmin + 10, ymin + 10)],
        crs=CRS,
    )
    result = apply_exclusions(grid, exclusions, overlap_threshold=0.5)
    assert result["status"].iloc[0] == "eligible"


def test_apply_exclusions_sums_overlap_across_multiple_polygons():
    """A cell overlapping TWO separate exclusion polygons, each below threshold
    alone, must be excluded once their combined overlap clears the threshold —
    and both reasons must appear (tests the groupby-sum + reason-union path)."""
    xmin, ymin = BBOX[0], BBOX[1]
    grid = gpd.GeoDataFrame(geometry=[box(xmin, ymin, xmin + 250, ymin + 250)], crs=CRS)
    # Two horizontal strips (top + bottom) that straddle the cell but neither
    # contains its centroid (at y=125) — so this must be decided by the OVERLAP
    # path, not the centroid-containment check (which runs first and would
    # otherwise mask a bug in the overlap path).
    exclusions = gpd.GeoDataFrame(
        {"exclusion_reason": ["flood_zone", "protected_area"]},
        geometry=[
            box(xmin, ymin, xmin + 250, ymin + 70),          # bottom: 250x70=17500 (28%)
            box(xmin, ymin + 180, xmin + 250, ymin + 250),   # top: 250x70=17500 (28%)
        ],
        crs=CRS,
    )
    result = apply_exclusions(grid, exclusions, overlap_threshold=0.5)
    # Combined overlap = 35000 / 62500 = 0.56 >= 0.5
    assert result["status"].iloc[0] == "excluded"
    reasons = result["exclusion_reasons"].iloc[0]
    assert "flood_zone" in reasons and "protected_area" in reasons
    assert result["status"].iloc[0] == "excluded"


# --- water (lakes, rivers, sea) ---------------------------------------------

def _cell():
    xmin, ymin = BBOX[0], BBOX[1]
    return xmin, ymin, gpd.GeoDataFrame(geometry=[box(xmin, ymin, xmin + 250, ymin + 250)], crs=CRS)


def _layer(tmp_path, name, geoms):
    path = tmp_path / name
    gpd.GeoDataFrame(geometry=geoms, crs=CRS).to_file(path, driver="GPKG")
    return path


def test_load_exclusions_tags_water_and_skips_a_missing_file(tmp_path):
    xmin, ymin, _ = _cell()
    water = _layer(tmp_path, "waterbodies.gpkg", [box(xmin, ymin, xmin + 100, ymin + 100)])
    result = load_exclusions(None, None, CRS, BBOX, water_path=water)
    assert list(result["exclusion_reason"]) == ["water"]
    # Missing (or omitted) water layer: skipped, like the other layers.
    assert load_exclusions(None, None, CRS, BBOX, water_path=tmp_path / "nope.gpkg").empty
    assert load_exclusions(None, None, CRS, BBOX).empty


def test_lake_or_sea_covering_most_of_a_cell_excludes_it(tmp_path):
    """Regression: waterbodies were never an exclusion, so ~18k sea/lake cells
    were scored as land (wind ~92 vs ~30 on land) and ranked parcels on them."""
    xmin, ymin, grid = _cell()
    lake = _layer(tmp_path, "w1.gpkg", [box(xmin - 500, ymin - 500, xmin + 500, ymin + 500)])  # whole cell
    shore = _layer(tmp_path, "w2.gpkg", [box(xmin, ymin, xmin + 250, ymin + 160)])           # 64%, covers centroid
    beach = _layer(tmp_path, "w3.gpkg", [box(xmin, ymin + 130, xmin + 250, ymin + 250)])     # 48%, misses centroid
    for path, expected in ((lake, "excluded"), (shore, "excluded"), (beach, "eligible")):
        result = apply_exclusions(grid, load_exclusions(None, None, CRS, BBOX, water_path=path))
        assert result["status"].iloc[0] == expected, path.name
        if expected == "excluded":
            assert result["exclusion_reasons"].iloc[0] == "water"


def test_land_cell_with_a_small_pond_at_its_centre_stays_eligible(tmp_path):
    """Water is decided by overlap only: a pond (or lake edge) over the cell's
    centroid must not exclude a cell that is mostly land. The protected/flood
    centroid rule would have."""
    xmin, ymin, grid = _cell()
    pond = _layer(tmp_path, "pond.gpkg", [box(xmin + 100, ymin + 100, xmin + 150, ymin + 150)])  # 4%, on the centroid
    lake_edge = _layer(tmp_path, "edge.gpkg", [box(xmin, ymin, xmin + 250, ymin + 140)])       # 56% -> excluded
    lake_edge_small = _layer(tmp_path, "edge2.gpkg", [box(xmin, ymin + 90, xmin + 250, ymin + 200)])  # 44% over centroid
    assert apply_exclusions(grid, load_exclusions(None, None, CRS, BBOX, water_path=pond))["status"].iloc[0] == "eligible"
    assert apply_exclusions(grid, load_exclusions(None, None, CRS, BBOX, water_path=lake_edge_small))["status"].iloc[0] == "eligible"
    assert apply_exclusions(grid, load_exclusions(None, None, CRS, BBOX, water_path=lake_edge))["status"].iloc[0] == "excluded"
    # Same small polygon as a protected area still excludes by centroid.
    assert apply_exclusions(grid, load_exclusions(pond, None, CRS, BBOX))["status"].iloc[0] == "excluded"


def test_area_under_two_layers_is_counted_once(tmp_path):
    """Regression: overlap was summed per polygon, so sea that is also a mapped
    flood zone counted twice (on the real data, 103 cells crossed 50% that way)."""
    xmin, ymin, grid = _cell()
    strip = box(xmin, ymin + 160, xmin + 250, ymin + 250)  # 36%, misses the centroid
    water = _layer(tmp_path, "water.gpkg", [strip])
    flood = _layer(tmp_path, "flood.gpkg", [strip])
    result = apply_exclusions(grid, load_exclusions(None, flood, CRS, BBOX, water_path=water))
    assert result["status"].iloc[0] == "eligible"  # 36% covered, not 72%
    # Disjoint pieces from different layers still add up.
    other = _layer(tmp_path, "flood2.gpkg", [box(xmin, ymin, xmin + 250, ymin + 70)])  # +28% -> 64%
    result = apply_exclusions(grid, load_exclusions(None, other, CRS, BBOX, water_path=water))
    assert result["status"].iloc[0] == "excluded"
    assert set(result["exclusion_reasons"].iloc[0].split("; ")) == {"water", "flood_zone"}


def test_invalid_water_polygons_are_repaired(tmp_path):
    """The real waterbodies layer has 3 invalid (self-intersecting) polygons."""
    xmin, ymin, grid = _cell()
    # Bow-tie over the whole cell: invalid as drawn, two triangles once repaired.
    bowtie = Polygon([(xmin - 300, ymin - 300), (xmin + 550, ymin + 550),
                      (xmin + 550, ymin - 300), (xmin - 300, ymin + 550)])
    assert not bowtie.is_valid
    water = _layer(tmp_path, "bad.gpkg", [bowtie])
    loaded = load_exclusions(None, None, CRS, BBOX, water_path=water)
    assert loaded.is_valid.all()
    assert loaded.geom_type.isin(["Polygon", "MultiPolygon"]).all()
    result = apply_exclusions(grid, loaded)  # must not raise
    assert result["status"].iloc[0] in ("eligible", "excluded")


# --- parcel assignment of shore cells ----------------------------------------

def test_shore_cell_with_centre_in_water_is_assigned_by_its_land(tmp_path):
    """Regression: a mostly-land cell whose centroid is just offshore was
    credited (by centroid) to the water lot holding that point. On the real
    data one such water lot ranked 2nd on a single land cell."""
    xmin, ymin, grid = _cell()
    lobe = box(xmin + 100, ymin, xmin + 150, ymin + 250)  # a 20% strip through the centre
    exclusions = load_exclusions(None, None, CRS, BBOX, water_path=_layer(tmp_path, "lobe.gpkg", [lobe]))
    cells = apply_exclusions(grid, exclusions)
    assert cells["status"].iloc[0] == "eligible"  # 20% water: still land
    pts = parcel_assignment_points(cells, exclusions)
    pt = pts.iloc[0]
    assert not lobe.contains(pt) and grid.geometry.iloc[0].contains(pt)


def test_assignment_points_default_to_centroids(tmp_path):
    xmin, ymin, grid = _cell()
    centre = grid.geometry.iloc[0].centroid
    # no exclusions at all
    assert parcel_assignment_points(grid, None).iloc[0].equals(centre)
    assert parcel_assignment_points(grid, load_exclusions(None, None, CRS, BBOX)).iloc[0].equals(centre)
    # a protected area over the centroid doesn't move the point (only water does)
    prot = _layer(tmp_path, "prot.gpkg", [box(xmin + 100, ymin + 100, xmin + 150, ymin + 150)])
    assert parcel_assignment_points(grid, load_exclusions(prot, None, CRS, BBOX)).iloc[0].equals(centre)
    # water away from the centroid doesn't move it either
    far = _layer(tmp_path, "far.gpkg", [box(xmin, ymin, xmin + 40, ymin + 40)])
    assert parcel_assignment_points(grid, load_exclusions(None, None, CRS, BBOX, water_path=far)).iloc[0].equals(centre)
    # a cell entirely under water has no land part: keeps its centroid, no error
    sea = _layer(tmp_path, "sea.gpkg", [box(xmin - 10, ymin - 10, xmin + 260, ymin + 260)])
    assert parcel_assignment_points(grid, load_exclusions(None, None, CRS, BBOX, water_path=sea)).iloc[0].equals(centre)


def test_shore_cell_goes_to_the_parcel_with_most_of_its_land(tmp_path):
    """With parcels given, the assignment point lands in the parcel holding
    the largest share of the cell's land, not wherever a point on the land
    happens to fall (that dropped some mostly-land parcels on the real data)."""
    xmin, ymin, grid = _cell()
    # A 12% lake lobe over the centroid; west parcel holds 24% of the cell's
    # area, east parcel the remaining 64% of land.
    lobe = box(xmin + 100, ymin + 100, xmin + 250, ymin + 150)
    exclusions = load_exclusions(None, None, CRS, BBOX, water_path=_layer(tmp_path, "lobe.gpkg", [lobe]))
    west = box(xmin - 500, ymin - 500, xmin + 60, ymin + 750)
    east = box(xmin + 60, ymin - 500, xmin + 750, ymin + 750)
    parcels = gpd.GeoDataFrame({"PID": ["west", "east"]}, geometry=[west, east], crs=CRS)
    pt = parcel_assignment_points(grid, exclusions, parcels).iloc[0]
    assert east.contains(pt) and not lobe.contains(pt)
    # flip the shares: the west parcel now holds most of the land
    west2 = box(xmin - 500, ymin - 500, xmin + 200, ymin + 750)
    east2 = box(xmin + 200, ymin - 500, xmin + 750, ymin + 750)
    parcels2 = gpd.GeoDataFrame({"PID": ["west", "east"]}, geometry=[west2, east2], crs=CRS)
    pt2 = parcel_assignment_points(grid, exclusions, parcels2).iloc[0]
    assert west2.contains(pt2) and not lobe.contains(pt2)



def _water(geoms, reason="water"):
    return gpd.GeoDataFrame({"exclusion_reason": [reason] * len(geoms)}, geometry=geoms, crs=CRS)


def test_land_acres_drop_the_water_in_a_parcel():
    """A lot half under a lake has half its acreage as land; a dry lot keeps it all."""
    parcels = gpd.GeoDataFrame(
        {"PID": ["lakefront", "dry"], "area_acres": [25.0, 10.0]},
        geometry=[box(0, 0, 1000, 100), box(2000, 0, 2100, 100)], crs=CRS,
    )
    lake = box(500, -50, 1200, 150)  # covers x 500-1000 of the lakefront lot
    result = parcel_land_acres(parcels, _water([lake]))
    assert list(result.columns) == ["land_acres", "water_share"]
    assert result["land_acres"].tolist() == pytest.approx([12.5, 10.0])
    assert result["water_share"].tolist() == pytest.approx([0.5, 0.0])


def test_land_acres_count_overlapping_water_once():
    """A river mapped over a lake must not take the same area out twice."""
    parcels = gpd.GeoDataFrame({"area_acres": [10.0]}, geometry=[box(0, 0, 100, 100)], crs=CRS)
    lake = box(0, 0, 50, 100)
    river = box(25, 0, 75, 100)  # half of it over the lake
    result = parcel_land_acres(parcels, _water([lake, river])).iloc[0]
    assert result["land_acres"] == pytest.approx(10.0 * 0.25)
    assert result["water_share"] == pytest.approx(0.75)


def test_land_acres_only_water_comes_off():
    """Flood zones and protected land are still land."""
    parcels = gpd.GeoDataFrame({"area_acres": [10.0]}, geometry=[box(0, 0, 100, 100)], crs=CRS)
    result = parcel_land_acres(parcels, _water([box(0, 0, 100, 100)], reason="flood_zone"))
    assert result["land_acres"].iloc[0] == pytest.approx(10.0)


def test_land_acres_without_a_water_layer_keep_acreage_and_say_so(caplog):
    """No water layer: acreage unchanged, water share unknown, and a warning."""
    parcels = gpd.GeoDataFrame({"area_acres": [10.0]}, geometry=[box(0, 0, 100, 100)], crs=CRS)
    empty = gpd.GeoDataFrame(columns=["geometry", "exclusion_reason"], crs=CRS)
    for exclusions in (None, empty):
        caplog.clear()
        with caplog.at_level("WARNING", logger="property_finder"):
            result = parcel_land_acres(parcels, exclusions)
        assert result["land_acres"].iloc[0] == pytest.approx(10.0)
        assert result["water_share"].isna().all()
        assert "no water subtracted" in caplog.text


def test_land_acres_without_area_column_use_mapped_area():
    parcels = gpd.GeoDataFrame(geometry=[box(0, 0, 4046.86, 2)], crs=CRS)  # 2 acres
    land = parcel_land_acres(parcels, _water([box(0, 0, 4046.86, 1)]))["land_acres"]
    assert land.iloc[0] == pytest.approx(1.0)


def test_land_acres_a_water_lot_has_none_and_scores_the_bottom_size_band():
    from src.scoring.preferences import score_parcel_size

    parcels = gpd.GeoDataFrame({"area_acres": [500.0]}, geometry=[box(0, 0, 100, 100)], crs=CRS)
    parcels[["land_acres", "water_share"]] = parcel_land_acres(
        parcels, _water([box(-10, -10, 110, 110)]))
    assert parcels["land_acres"].iloc[0] == pytest.approx(0.0)
    assert parcels["water_share"].iloc[0] == pytest.approx(1.0)
    assert score_parcel_size(parcels).iloc[0] == 0  # was 100 on its 500 deeded acres


def test_land_acres_repair_an_invalid_water_polygon():
    """A self-intersecting (bowtie) lake is repaired, not allowed to raise or
    return a wrong area: its two triangles each cover a quarter of the parcel."""
    parcels = gpd.GeoDataFrame({"area_acres": [10.0]}, geometry=[box(0, 0, 100, 100)], crs=CRS)
    bowtie = Polygon([(0, 0), (100, 100), (100, 0), (0, 100), (0, 0)])
    assert not bowtie.is_valid
    result = parcel_land_acres(parcels, _water([bowtie])).iloc[0]
    assert result["water_share"] == pytest.approx(0.5)
    assert result["land_acres"] == pytest.approx(5.0)

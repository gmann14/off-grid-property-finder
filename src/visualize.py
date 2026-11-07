"""Interactive Folium map generation from scored output."""

import json
import logging
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

from src.config import Config

logger = logging.getLogger("property_finder")


def _tier(score: float | None):
    """The SCORE_TIERS row a score falls in, or None for no score (None/NaN)."""
    from src.constants import SCORE_TIERS

    if score is None or pd.isna(score):
        return None
    for tier in SCORE_TIERS:
        if score >= tier[0]:
            return tier
    return SCORE_TIERS[-1]          # below the lowest floor (negative): worst tier


def _score_color(score: float | None) -> str:
    """Map a 0-100 score to its tier colour (grey for no score: None or NaN)."""
    from src.constants import NO_SCORE_COLOUR

    tier = _tier(score)
    return NO_SCORE_COLOUR if tier is None else tier[2]


def _score_opacity(score: float | None) -> float:
    """Higher tiers are more opaque so top candidates stand out."""
    tier = _tier(score)
    return 0.15 if tier is None else tier[3]


def _tier_layers() -> list[tuple[str, float, float, bool]]:
    """(layer name, floor, ceiling, shown by default) per tier, best first; only
    the top tier is on by default."""
    from src.constants import SCORE_TIERS

    layers, ceiling = [], None
    for i, (floor, label, _colour, _op) in enumerate(SCORE_TIERS):
        span = f"{floor}-100" if ceiling is None else f"{floor}-{ceiling - 1}"
        if i == len(SCORE_TIERS) - 1:
            span = f"0-{ceiling - 1}" if ceiling is not None else "0-100"
        layers.append((f"{label} ({span})", floor, ceiling, i == 0))
        ceiling = floor
    return layers


def _legend_rows() -> str:
    from src.constants import NO_SCORE_COLOUR, SCORE_TIERS

    swatch = ('<i style="background:{c};width:16px;height:16px;display:inline-block;'
              'margin:3px 6px 0 0;vertical-align:middle;border:1px solid #999;"></i> {t}<br>')
    rows = [swatch.format(c=colour, t=name.replace("-", "–").replace(f"{label} (", "").rstrip(")") + f" ({label})")
            for (name, *_), (_f, label, colour, _o) in zip(_tier_layers(), SCORE_TIERS)]
    rows.append(swatch.format(c=NO_SCORE_COLOUR, t="Excluded (protected, flood or water)"))
    return "\n        ".join(rows)


def _truncate_coords(geojson: dict, precision: int = 5) -> None:
    """Reduce coordinate precision in-place to shrink GeoJSON size."""
    def _trunc(coords):
        if isinstance(coords[0], (list, tuple)):
            return [_trunc(c) for c in coords]
        return [round(c, precision) for c in coords]

    for feature in geojson.get("features", []):
        geom = feature.get("geometry")
        if geom and "coordinates" in geom:
            geom["coordinates"] = _trunc(geom["coordinates"])


def _add_study_area_boundary(m, config) -> None:
    """Add a dashed rectangle showing the study area extent."""
    import folium

    bbox = config.study_area.bbox  # (xmin, ymin, xmax, ymax) in EPSG:2961
    from shapely.geometry import box
    study_box = gpd.GeoDataFrame(
        geometry=[box(*bbox)], crs=config.working_crs
    ).to_crs("EPSG:4326")
    b = study_box.total_bounds  # [minx, miny, maxx, maxy]

    study_group = folium.FeatureGroup(name="Study Area Boundary", show=True)
    folium.Rectangle(
        bounds=[[b[1], b[0]], [b[3], b[2]]],
        color="#ff6600",
        weight=3,
        fill=False,
        dash_array="10 6",
        tooltip=f"Study Area: {config.study_area.name}",
    ).add_to(study_group)
    study_group.add_to(m)


def _add_contours(m, dem_path: Path, processed: Path):
    """Generate elevation contour lines from DEM and add as a map layer."""
    import folium
    import rasterio

    try:
        from skimage import measure
    except ImportError:
        logger.warning("scikit-image not installed; skipping contours")
        return

    with rasterio.open(dem_path) as src:
        dem = src.read(1).astype(np.float32)
        transform = src.transform
        nodata = src.nodata
        crs = src.crs

    if nodata is not None:
        dem[dem == nodata] = np.nan

    valid = dem[~np.isnan(dem)]
    if len(valid) == 0:
        return

    elev_min, elev_max = float(valid.min()), float(valid.max())
    interval = 50  # 50m contour interval

    contour_features = []
    for level in range(int(elev_min // interval) * interval, int(elev_max) + interval, interval):
        if level <= 0:
            continue
        try:
            contours = measure.find_contours(dem, float(level))
        except Exception:
            continue
        for contour in contours:
            if len(contour) < 5:
                continue
            # Convert pixel coords to geographic coords
            coords = []
            for row, col in contour:
                x = transform.c + col * transform.a + row * transform.b
                y = transform.f + col * transform.d + row * transform.e
                coords.append((x, y))

            from shapely.geometry import LineString
            line = LineString(coords)
            contour_features.append({
                "geometry": line,
                "elevation": level,
            })

    if not contour_features:
        return

    contour_gdf = gpd.GeoDataFrame(contour_features, crs=crs)
    contour_gdf = contour_gdf.to_crs("EPSG:4326")

    # Major contours every 100m, minor every 20m
    major = contour_gdf[contour_gdf["elevation"] % 100 == 0]
    minor = contour_gdf[contour_gdf["elevation"] % 100 != 0]

    contour_group = folium.FeatureGroup(name="Elevation Contours (50m)", show=False)

    if not minor.empty:
        minor_json = json.loads(minor.to_json())
        _truncate_coords(minor_json, precision=4)
        folium.GeoJson(
            minor_json,
            style_function=lambda f: {
                "color": "#8B7355",
                "weight": 0.5,
                "opacity": 0.4,
            },
        ).add_to(contour_group)

    if not major.empty:
        major_json = json.loads(major.to_json())
        _truncate_coords(major_json, precision=4)
        folium.GeoJson(
            major_json,
            style_function=lambda f: {
                "color": "#6B4226",
                "weight": 1.5,
                "opacity": 0.7,
            },
            tooltip=folium.GeoJsonTooltip(
                fields=["elevation"],
                aliases=["Elevation (m)"],
            ),
        ).add_to(contour_group)

    contour_group.add_to(m)
    logger.info("Added %d contour lines to map", len(contour_features))


def _add_streams(m, streams_path: Path):
    """Add stream/river network layer (excludes lake shorelines and coastline)."""
    import folium

    streams = gpd.read_file(streams_path)

    # Filter to flowing water — exclude lake shorelines, tidal, wharves, etc.
    if "FEAT_CODE" in streams.columns:
        exclude_prefixes = ("WALK", "WATO", "WAWH", "WADM", "WABD")
        excluded = streams["FEAT_CODE"].str.startswith(exclude_prefixes)
        excluded |= streams["FEAT_CODE"].str.contains("IS", na=False)
        streams = streams[~excluded]

    if "LINE_CLASS" in streams.columns:
        streams = streams[streams["LINE_CLASS"].fillna(3).astype(int) <= 4]

    streams = streams.to_crs("EPSG:4326")

    def stream_style(feature):
        lc = feature["properties"].get("LINE_CLASS", 3)
        if lc is None:
            lc = 3
        lc = int(lc)
        weight = max(0.5, 4.0 - (lc - 1) * 0.7)
        opacity = max(0.3, 0.9 - (lc - 1) * 0.1)
        return {
            "color": "#2166ac",
            "weight": weight,
            "opacity": opacity,
        }

    tooltip_fields = []
    tooltip_aliases = []
    if "RIVNAME_1" in streams.columns:
        tooltip_fields.append("RIVNAME_1")
        tooltip_aliases.append("River")
    if "LINE_CLASS" in streams.columns:
        tooltip_fields.append("LINE_CLASS")
        tooltip_aliases.append("Class")

    streams.geometry = streams.geometry.simplify(tolerance=0.0001)
    keep = [c for c in ["geometry", "RIVNAME_1", "LINE_CLASS"] if c in streams.columns]
    streams = streams[keep]

    stream_group = folium.FeatureGroup(name="Streams & Rivers", show=False)
    geojson = json.loads(streams.to_json())
    _truncate_coords(geojson, precision=5)
    folium.GeoJson(
        geojson,
        style_function=stream_style,
        tooltip=folium.GeoJsonTooltip(
            fields=tooltip_fields,
            aliases=tooltip_aliases,
        ) if tooltip_fields else None,
    ).add_to(stream_group)
    stream_group.add_to(m)
    logger.info("Added %d stream/river features to map", len(streams))


def _add_roads(m, roads_path: Path):
    """Add road network styled by highway classification."""
    import folium

    roads = gpd.read_file(roads_path)
    # Drop minor paths/footways to reduce file size
    if "highway" in roads.columns:
        skip = {"footway", "path", "steps", "cycleway", "service"}
        roads = roads[~roads["highway"].isin(skip)]
    roads = roads.to_crs("EPSG:4326")

    road_styles = {
        "motorway": ("#e31a1c", 3.0),
        "motorway_link": ("#e31a1c", 2.0),
        "trunk": ("#fd8d3c", 2.5),
        "primary": ("#fd8d3c", 2.0),
        "secondary": ("#feb24c", 1.5),
        "secondary_link": ("#feb24c", 1.2),
        "tertiary": ("#888888", 1.2),
        "unclassified": ("#aaaaaa", 0.8),
        "residential": ("#cccccc", 0.6),
        "service": ("#cccccc", 0.4),
        "track": ("#996633", 0.8),
    }

    def road_style(feature):
        hw = feature["properties"].get("highway", "")
        color, weight = road_styles.get(hw, ("#cccccc", 0.5))
        return {
            "color": color,
            "weight": weight,
            "opacity": 0.8,
        }

    tooltip_fields = []
    tooltip_aliases = []
    if "name" in roads.columns:
        tooltip_fields.append("name")
        tooltip_aliases.append("Name")
    if "highway" in roads.columns:
        tooltip_fields.append("highway")
        tooltip_aliases.append("Type")

    roads.geometry = roads.geometry.simplify(tolerance=0.0001)
    keep = [c for c in ["geometry", "name", "highway"] if c in roads.columns]
    roads = roads[keep]

    road_group = folium.FeatureGroup(name="Roads", show=False)
    geojson = json.loads(roads.to_json())
    _truncate_coords(geojson, precision=5)
    folium.GeoJson(
        geojson,
        style_function=road_style,
        tooltip=folium.GeoJsonTooltip(
            fields=tooltip_fields,
            aliases=tooltip_aliases,
        ) if tooltip_fields else None,
    ).add_to(road_group)
    road_group.add_to(m)
    logger.info("Added %d road features to map", len(roads))


def _add_crown_land(m, crown_path: Path):
    """Add crown land parcels as a toggleable layer."""
    import folium

    cl = gpd.read_file(crown_path)
    cl = cl.to_crs("EPSG:4326")

    cl.geometry = cl.geometry.simplify(tolerance=0.0001)
    keep = [c for c in ["geometry", "Acres", "DNR_ID"] if c in cl.columns]
    cl = cl[keep]

    crown_group = folium.FeatureGroup(name="Crown Land", show=False)
    geojson = json.loads(cl.to_json())
    _truncate_coords(geojson, precision=5)

    tooltip_fields = []
    tooltip_aliases = []
    if "Acres" in cl.columns:
        tooltip_fields.append("Acres")
        tooltip_aliases.append("Acres")
    if "DNR_ID" in cl.columns:
        tooltip_fields.append("DNR_ID")
        tooltip_aliases.append("DNR ID")

    folium.GeoJson(
        geojson,
        style_function=lambda f: {
            "fillColor": "#9467bd",
            "color": "#7b4ea3",
            "weight": 1.5,
            "fillOpacity": 0.25,
        },
        tooltip=folium.GeoJsonTooltip(
            fields=tooltip_fields,
            aliases=tooltip_aliases,
        ) if tooltip_fields else None,
    ).add_to(crown_group)
    crown_group.add_to(m)
    logger.info("Added %d crown land parcels to map", len(cl))


def _add_protected_areas(m, protected_path: Path):
    """Add protected areas as a red-hatched overlay."""
    import folium

    pa = gpd.read_file(protected_path)
    if pa.crs and str(pa.crs) != "EPSG:4326":
        pa = pa.to_crs("EPSG:4326")

    pa = pa.simplify(0.0005)
    pa_gdf = gpd.GeoDataFrame(geometry=pa)

    # Re-attach name/type columns from original
    pa_orig = gpd.read_file(protected_path)
    for col in ("pro_name", "protect1"):
        if col in pa_orig.columns:
            pa_gdf[col] = pa_orig[col].values

    pa_group = folium.FeatureGroup(name="Protected Areas", show=False)
    geojson = json.loads(pa_gdf.to_json())
    _truncate_coords(geojson, precision=4)

    tooltip_fields = [c for c in ["pro_name", "protect1"] if c in pa_gdf.columns]
    tooltip_aliases = {"pro_name": "Name", "protect1": "Type"}

    folium.GeoJson(
        geojson,
        style_function=lambda f: {
            "fillColor": "#d73027",
            "color": "#d73027",
            "weight": 1.5,
            "fillOpacity": 0.25,
            "dashArray": "5 5",
        },
        tooltip=folium.GeoJsonTooltip(
            fields=tooltip_fields,
            aliases=[tooltip_aliases.get(f, f) for f in tooltip_fields],
        ) if tooltip_fields else None,
    ).add_to(pa_group)
    pa_group.add_to(m)
    logger.info("Added %d protected area features to map", len(pa_gdf))


def _add_buildings(m, buildings_path: Path):
    """Add building centroids as small circle markers."""
    import folium

    buildings = gpd.read_file(buildings_path)
    # Compute centroids in projected CRS, then convert to WGS84 points
    centroids = buildings.geometry.centroid
    points_gdf = gpd.GeoDataFrame(geometry=centroids, crs=buildings.crs).to_crs("EPSG:4326")

    bldg_group = folium.FeatureGroup(name="Buildings", show=False)
    geojson = json.loads(points_gdf.to_json())
    _truncate_coords(geojson, precision=4)
    folium.GeoJson(
        geojson,
        style_function=lambda f: {
            "fillColor": "#555555",
            "color": "#555555",
            "weight": 0,
            "fillOpacity": 0.7,
        },
        marker=folium.CircleMarker(radius=2, fill=True),
    ).add_to(bldg_group)
    bldg_group.add_to(m)
    logger.info("Added %d building markers to map", len(buildings))


def _parcel_popup_js(config: Config | None) -> str:
    """Leaflet ``onEachFeature`` that binds a click popup explaining a parcel's
    rank: parcel score = cell part + size part, the top cells' criterion means
    (which rebuild ``cell_score``), land vs mapped acres, and lookup links.

    Built in the browser from each feature's properties, so it adds no HTML per
    parcel to map.html. Weights and the cell/size split come from ``config``
    (the defaults when it is None), matching the scoring run.
    """
    from src.constants import DEFAULT_WEIGHTS, PARCEL_CELL_WEIGHT, PARCEL_TOP_N_CELLS

    if config is None:
        weights = {k: float(v) for k, v in DEFAULT_WEIGHTS.items()}
        total = sum(weights.values()) or 1.0
        weights = {k: v / total * 100 for k, v in weights.items()}
        agg = {}
    else:
        weights = config.enabled_weights()
        agg = config.preferences.get("parcel_aggregation", {}) or {}
    cell_w = float(agg.get("cell_weight", PARCEL_CELL_WEIGHT))
    top_n = int(agg.get("top_n", PARCEL_TOP_N_CELLS))
    criteria = [[name, _CRITERION_INFO.get(name, (name.replace("_", " ").capitalize(), "", ""))[0],
                 round(w, 2)] for name, w in weights.items() if w > 0]
    return f"""function(feature, layer) {{
    var p = feature.properties || {{}};
    var CRITERIA = {json.dumps(criteria)};
    var CELL_W = {cell_w!r}, TOP_N = {top_n};
    function esc(s) {{ return String(s).replace(/[&<>"']/g, function(c) {{
        return {{"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}}[c]; }}); }}
    function num(v, d) {{
        return (v === null || v === undefined || v === "" || (typeof v === "number" && !isFinite(v)))
            ? "n/a" : Number(v).toFixed(d); }}
    function row(k, v) {{ return "<tr><td style='padding:1px 8px 1px 0;color:#555'>" + k +
        "</td><td style='text-align:right'><b>" + v + "</b></td></tr>"; }}
    var html = "<div style='font:12px Arial,sans-serif;min-width:230px'>" +
        "<b style='font-size:13px'>PID " + esc(p.PID === undefined ? "n/a" : p.PID) + "</b>" +
        " &middot; rank " + num(p.rank, 0) + "<table>" +
        row("Parcel score", num(p.score, 1)) +
        row(Math.round(CELL_W * 100) + "% &times; cell score (top " + TOP_N + " cells)", num(p.cell_score, 1)) +
        row(Math.round((1 - CELL_W) * 100) + "% &times; land-acreage score", num(p.size_score, 1)) +
        "</table><div style='margin-top:4px;color:#555'>Top cells' criterion means (weight):</div><table>";
    for (var i = 0; i < CRITERIA.length; i++) {{
        var c = CRITERIA[i];
        if (("top_" + c[0]) in p) html += row(esc(c[1]) + " (" + Math.round(c[2]) + "%)", num(p["top_" + c[0]], 0));
    }}
    var water = (p.water_share === null || p.water_share === undefined) ? "n/a" : num(100 * p.water_share, 1) + "%";
    html += "</table><table style='margin-top:4px'>" +
        row("Land acres / mapped", num(p.land_acres, 1) + " / " + num(p.area_acres, 1)) +
        row("Water in parcel", water) +
        row("Scored cells", num(p.n_cells, 0)) +
        row("Type", esc(p.parcel_type === undefined || p.parcel_type === null ? "n/a" : p.parcel_type) +
            (p.n_buildings ? " (" + num(p.n_buildings, 0) + " bldg)" : "")) +
        (p.severance_candidate === true || p.severance_candidate === "True" ? row("Severance candidate", "yes") : "") +
        row("Confidence", num(p.confidence, 0)) + "</table>";
    if (p.flags) html += "<div style='color:#a15c00;margin-top:3px'>" + esc(p.flags) + "</div>";
    var c0 = layer.getBounds ? layer.getBounds().getCenter() : null;
    html += "<div style='margin-top:5px'>Look up the PID on <a href='https://www.viewpoint.ca' target='_blank' rel='noopener'>ViewPoint</a>" +
        (c0 ? " &middot; <a href='https://www.google.com/maps?q=" + c0.lat.toFixed(6) + "," + c0.lng.toFixed(6) +
              "' target='_blank' rel='noopener'>Maps</a>" : "") + "</div></div>";
    layer.bindPopup(html, {{maxWidth: 340}});
}}"""


def _add_parcels(m, parcels_path: Path, config: Config | None = None):
    """Add scored parcels (Stage B) colored by parcel score, PID in tooltip.

    Only parcels with an actual score are included. Unscored parcels (no
    candidate cells assigned — the common case: a bbox pull returns every
    parcel in the study area, but only a fraction overlap the eligible
    candidate grid) render as near-invisible and carry no useful PID/score
    info, so including them was pure payload bloat: on the real study area
    this cut the parcel layer from 42,010 rows to 8,980 (and the overall
    map.html from ~59MB toward a fraction of that).
    """
    import folium

    parcels = gpd.read_file(parcels_path)
    if "score" in parcels.columns:
        n_before = len(parcels)
        parcels = parcels[parcels["score"].notna()].copy()
        if len(parcels) < n_before:
            logger.info("Parcel layer: dropped %d unscored parcels (kept %d)",
                        n_before - len(parcels), len(parcels))
    parcels = parcels.to_crs("EPSG:4326")
    parcels.geometry = parcels.geometry.simplify(tolerance=0.00002)

    top_cols = [c for c in parcels.columns if c.startswith("top_")]
    keep = [c for c in ["geometry", "PID", "AAN", "score", "rank",
                        "cell_score", "size_score", "n_cells", "area_acres", "land_acres",
                        "water_share", *top_cols, "confidence", "parcel_type",
                        "n_buildings", "severance_candidate", "flags"]
            if c in parcels.columns]
    parcels = parcels[keep].copy()
    # One decimal is all the popup and tooltip show; full precision is in the CSV.
    for col in ["score", "cell_score", "size_score", "area_acres", "land_acres",
                "confidence", *top_cols]:
        if col in parcels.columns:
            parcels[col] = parcels[col].astype(float).round(1)
    if "water_share" in parcels.columns:
        parcels["water_share"] = parcels["water_share"].astype(float).round(3)

    parcel_group = folium.FeatureGroup(name="Scored Parcels (PID)", show=False)
    geojson = json.loads(parcels.to_json())
    _truncate_coords(geojson, precision=5)

    def parcel_style(feature):
        score = feature["properties"].get("score")
        return {
            "fillColor": _score_color(score),
            "color": "#333333",
            "weight": 0.6,
            "fillOpacity": _score_opacity(score) if score is not None else 0.05,
        }

    fields = [c for c in ["PID", "score", "rank", "cell_score", "n_cells",
                          "area_acres", "land_acres", "parcel_type", "n_buildings"]
              if c in parcels.columns]
    aliases = {"PID": "PID", "score": "Parcel score", "rank": "Rank",
               "cell_score": "Cell score", "n_cells": "Cells", "area_acres": "Acres",
               "land_acres": "Land acres",
               "parcel_type": "Type", "n_buildings": "Buildings"}

    folium.GeoJson(
        geojson,
        style_function=parcel_style,
        tooltip=folium.GeoJsonTooltip(
            fields=fields,
            aliases=[aliases.get(f, f) for f in fields],
            localize=True,
        ) if fields else None,
        on_each_feature=folium.JsCode(_parcel_popup_js(config)),
    ).add_to(parcel_group)
    parcel_group.add_to(m)
    logger.info("Added %d scored parcels to map", len(parcels))


EXCLUDED_LAYER_NAME = "Excluded (protected, flood or water)"


def _add_excluded_cells(m, candidates_wgs: gpd.GeoDataFrame) -> None:
    """Draw excluded cells (no score) as one grey layer, off by default.

    The cells are dissolved by exclusion reason, so ~19k squares become a few
    multipolygons: the map shows where and why land was excluded without
    carrying each cell. Reasons are sorted so "water; flood_zone" and
    "flood_zone; water" land in one group.
    """
    import folium

    if "score" not in candidates_wgs.columns:
        return
    excluded = candidates_wgs[candidates_wgs["score"].isna()]
    if "status" in excluded.columns:
        excluded = excluded[excluded["status"].fillna("excluded") == "excluded"]
    if excluded.empty:
        return
    if "exclusion_reasons" in excluded.columns:
        reasons = excluded["exclusion_reasons"].fillna("").map(
            lambda r: "; ".join(sorted(x.strip() for x in str(r).split(";") if x.strip())) or "excluded")
    else:
        reasons = pd.Series("excluded", index=excluded.index)
    dissolved = (
        gpd.GeoDataFrame({"reasons": reasons.values, "cells": 1},
                         geometry=excluded.geometry.values, crs=excluded.crs)
        .dissolve(by="reasons", aggfunc={"cells": "sum"})
        .reset_index()
    )
    dissolved.geometry = dissolved.geometry.simplify(tolerance=0.00002)
    geojson = json.loads(dissolved.to_json())
    _truncate_coords(geojson, precision=5)
    folium.GeoJson(
        geojson,
        name=EXCLUDED_LAYER_NAME,
        show=False,
        style_function=lambda _f: {"fillColor": "#808080", "color": "#808080",
                                   "weight": 0.3, "fillOpacity": 0.35},
        tooltip=folium.GeoJsonTooltip(fields=["reasons", "cells"],
                                      aliases=["Excluded for", "Cells"], localize=True),
    ).add_to(m)
    logger.info("Added %d excluded cells to map (%d reason groups)", len(excluded), len(dissolved))


# Label, colour and one-line description for each criterion in the formula panel.
_CRITERION_INFO = {
    "hydro": ("Micro-Hydro", "#2166ac", "Nearby stream, flow (drainage), head"),
    "access": ("Access", "#e31a1c", "Distance to nearest road"),
    "open_ground": ("Open ground", "#4a9e4a", "Flat, open, unbuilt land (solar room)"),
    "wind": ("Wind", "#7e57c2", "Wind resource / ridge exposure"),
    "elevation": ("Elevation", "#6B4226", "Coastal-flood penalty (low ground)"),
    "solar": ("Solar", "#f4a582", "Solar exposure"),
    "buildable": ("Buildable", "#8c8c8c", "Buildable share of the cell"),
}


def _formula_panel_html(config: Config) -> str:
    """The map's scoring-formula panel, built from the config the map was made
    with, so the weights and parcel formula shown are the ones used to score."""
    from src.constants import PARCEL_CELL_WEIGHT, PARCEL_TOP_N_CELLS

    weights = {k: v for k, v in config.enabled_weights().items() if v > 0}
    rows = []
    for i, (name, weight) in enumerate(sorted(weights.items(), key=lambda kv: -kv[1])):
        label, colour, desc = _CRITERION_INFO.get(
            name, (name.replace("_", " ").capitalize(), "#333333", ""))
        border = ' style="border-bottom:1px solid #eee;"' if i < len(weights) - 1 else ""
        rows.append(
            f'<tr{border}><td style="padding:4px 0;"><b style="color:{colour};">{label}</b></td>'
            f'<td style="padding:4px 8px; text-align:right; white-space:nowrap;"><b>{weight:.0f}%</b></td>'
            f'<td style="padding:4px 0; color:#555;">{desc}</td></tr>'
        )
    agg = config.preferences.get("parcel_aggregation", {}) or {}
    top_n = agg.get("top_n", PARCEL_TOP_N_CELLS)
    cell_w = float(agg.get("cell_weight", PARCEL_CELL_WEIGHT))
    min_acres = f"{config.min_parcel_area_acres:g}"
    acre_word = "acre" if min_acres == "1" else "acres"
    return f"""
    <div id="formula-panel" style="
        position: fixed;
        top: 12px; left: 55px;
        background: white;
        border: 2px solid #666;
        border-radius: 6px;
        padding: 14px 18px;
        font-size: 13px;
        font-family: Arial, sans-serif;
        z-index: 9999;
        box-shadow: 2px 2px 6px rgba(0,0,0,0.3);
        max-width: 340px;
        line-height: 1.5;
    ">
        <div style="display:flex; justify-content:space-between; align-items:center;">
            <b style="font-size: 15px;">Off-Grid Suitability Score</b>
            <span onclick="document.getElementById('formula-panel').style.display='none'"
                  style="cursor:pointer; color:#999; font-size:18px; margin-left:12px;">&times;</span>
        </div>
        <hr style="margin: 6px 0;">
        <table style="width:100%; border-collapse:collapse; font-size: 12.5px;">
            {"".join(rows)}
        </table>
        <hr style="margin: 6px 0;">
        <span style="font-size: 11px; color: #888;">
            Cell score (shown) = weighted sum. An "all-rounder" geometric-mean<br>
            score is also exported for true do-it-all parcels.<br>
            Parcel score = {cell_w:.2f} &times; mean(top {top_n} cells) + {1 - cell_w:.2f} &times; land-acreage score.<br>
            Cells in protected areas, flood zones or mostly water are excluded.<br>
            Parcel acreage counts land only; parcels under {min_acres} {acre_word} of land are unranked.
        </span>
    </div>
    """


def run_visualize(config: Config, logger: logging.Logger) -> bool:
    """Generate an interactive Folium map from scored output.

    Returns False (after logging why) when there is nothing to map or folium is
    missing, so the CLI can exit non-zero. Raises StaleProcessedDataError when
    data/processed/ was built from a different config, as ``score`` does.
    """
    from src.manifest import verify_manifest_fresh

    try:
        import folium
    except ImportError:
        logger.error("folium not installed. Install with: pip install folium")
        return False

    verify_manifest_fresh(config)

    output = config.paths.output
    processed = config.paths.processed
    scored_path = output / "scored_cells.gpkg"

    if not scored_path.exists():
        logger.error("Scored cells not found at %s. Run 'score' first.", scored_path)
        return False

    candidates = gpd.read_file(scored_path)
    logger.info("Loaded %d scored cells for visualization", len(candidates))

    # Convert to WGS84 for Folium
    candidates_wgs = candidates.to_crs("EPSG:4326")

    # Compute map center
    bounds = candidates_wgs.total_bounds  # [minx, miny, maxx, maxy]
    center_lat = (bounds[1] + bounds[3]) / 2
    center_lon = (bounds[0] + bounds[2]) / 2

    m = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=11,
        tiles=None,
    )

    # --- Base map tile layers (first one added = default) ---

    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}",
        attr="Esri",
        name="Topographic",
        control=True,
        show=True,
    ).add_to(m)

    folium.TileLayer(
        tiles="https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
        attr='&copy; <a href="https://carto.com/">CARTO</a> &copy; <a href="https://www.openstreetmap.org/copyright">OSM</a>',
        name="CartoDB Light",
        show=False,
    ).add_to(m)

    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri",
        name="Satellite",
        show=False,
    ).add_to(m)

    folium.TileLayer(
        tiles="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
        attr='&copy; <a href="https://carto.com/">CARTO</a>',
        name="CartoDB Dark",
        show=False,
    ).add_to(m)

    # --- Score band layers (only Excellent on by default) ---
    band_defs = [
        (name, (lambda s, lo=floor, hi=ceiling:
                pd.notna(s) and (s >= lo or lo == 0) and (hi is None or s < hi)), show)
        for name, floor, ceiling, show in _tier_layers()
    ]

    # Columns to include in GeoJSON (drop heavy/unnecessary ones)
    keep_cols = ["geometry", "status", "score", "score_allrounder", "rank",
                 "score_hydro", "score_access", "score_open_ground", "score_wind",
                 "score_elevation", "wind_worth_it", "confidence", "confidence_band"]
    keep_cols = [c for c in keep_cols if c in candidates_wgs.columns]

    for band_name, pred, show in band_defs:
        mask = candidates_wgs["score"].apply(pred)
        subset = candidates_wgs.loc[mask, keep_cols]
        if subset.empty:
            continue

        geojson_data = json.loads(subset.to_json())
        _truncate_coords(geojson_data, precision=5)

        def make_style(feature, _pred=pred):
            score = feature["properties"].get("score")
            return {
                "fillColor": _score_color(score),
                "color": _score_color(score),
                "weight": 0.3,
                "fillOpacity": _score_opacity(score),
            }

        layer = folium.GeoJson(
            geojson_data,
            name=band_name,
            show=show,
            style_function=make_style,
            tooltip=folium.GeoJsonTooltip(
                fields=["status", "score", "rank"],
                aliases=["Status", "Score", "Rank"],
                localize=True,
            ),
        )
        layer.add_to(m)

    _add_excluded_cells(m, candidates_wgs)

    # --- Study area boundary ---
    _add_study_area_boundary(m, config)

    # --- Data overlay layers (off by default, toggled on by user) ---
    streams_path = processed / "streams.gpkg"
    if streams_path.exists():
        _add_streams(m, streams_path)

    roads_path = processed / "roads.gpkg"
    if roads_path.exists():
        _add_roads(m, roads_path)

    crown_path = processed / "crown_land.gpkg"
    if crown_path.exists():
        _add_crown_land(m, crown_path)

    protected_path = processed / "protected_areas.gpkg"
    if protected_path.exists():
        _add_protected_areas(m, protected_path)

    buildings_path = processed / "buildings.gpkg"
    if buildings_path.exists():
        _add_buildings(m, buildings_path)

    parcels_path = output / "scored_parcels.gpkg"
    if parcels_path.exists():
        _add_parcels(m, parcels_path, config)

    dem_path = processed / "dem.tif"
    if dem_path.exists():
        _add_contours(m, dem_path, processed)

    # --- Layer control & fit ---
    folium.LayerControl(collapsed=False).add_to(m)
    m.fit_bounds([[bounds[1], bounds[0]], [bounds[3], bounds[2]]])

    # --- Legend ---
    legend_html = """
    <div style="
        position: fixed;
        bottom: 30px; right: 30px;
        background: white;
        border: 2px solid #666;
        border-radius: 6px;
        padding: 12px 16px;
        font-size: 13px;
        font-family: Arial, sans-serif;
        z-index: 9999;
        box-shadow: 2px 2px 6px rgba(0,0,0,0.3);
        max-height: 80vh;
        overflow-y: auto;
    ">
        <b style="font-size: 14px;">Suitability Score</b><br>
        """ + _legend_rows() + """
        <hr style="margin: 6px 0;">
        <b style="font-size: 13px;">Overlays</b><br>
        <span style="color:#2166ac;">&#9473;&#9473;</span> Streams & Rivers<br>
        <span style="color:#e31a1c;">&#9473;</span><span style="color:#fd8d3c;">&#9473;</span><span style="color:#888;">&#9473;</span> Roads (major → minor)<br>
        <i style="background:#9467bd;opacity:0.4;width:16px;height:16px;display:inline-block;margin:3px 6px 0 0;vertical-align:middle;border:1px solid #7b4ea3;"></i> Crown Land<br>
        <i style="background:#d73027;opacity:0.3;width:16px;height:16px;display:inline-block;margin:3px 6px 0 0;vertical-align:middle;border:1px dashed #d73027;"></i> Protected Areas<br>
        <i style="background:#555;width:16px;height:16px;display:inline-block;margin:3px 6px 0 0;vertical-align:middle;border:1px solid #333;"></i> Buildings<br>
        <i style="background:#2c7bb6;opacity:0.5;width:16px;height:16px;display:inline-block;margin:3px 6px 0 0;vertical-align:middle;border:1px solid #333;"></i> Scored Parcels (PID, by score)<br>
        <span style="color:#6B4226;">&#9473;&#9473;</span> Elevation Contours (50m)<br>
        <br>
        <span style="font-size: 11px; color: #666;">
            Toggle layers in control panel.<br>
            Hover cells for score details.
        </span>
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))

    # --- Scoring formula panel ---
    formula_html = _formula_panel_html(config)
    m.get_root().html.add_child(folium.Element(formula_html))

    # Save map
    map_path = output / "map.html"
    m.save(str(map_path))
    logger.info("Saved interactive map: %s", map_path)
    return True

"""Interactive Folium map generation from scored output."""

import json
import logging
from pathlib import Path

import geopandas as gpd
import numpy as np

from src.config import Config

logger = logging.getLogger("property_finder")


def _score_color(score: float | None) -> str:
    """Map a 0-100 score to a color for the map."""
    if score is None:
        return "#808080"
    if score >= 80:
        return "#1a9641"
    if score >= 60:
        return "#a6d96a"
    if score >= 40:
        return "#ffffbf"
    if score >= 20:
        return "#fdae61"
    return "#d7191c"


def _score_opacity(score: float | None) -> float:
    """Higher scores get more opaque so top candidates stand out."""
    if score is None:
        return 0.15
    if score >= 80:
        return 0.75
    if score >= 60:
        return 0.55
    if score >= 40:
        return 0.40
    if score >= 20:
        return 0.35
    return 0.30


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
    from rasterio.features import shapes as rasterio_shapes

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


def run_visualize(config: Config, logger: logging.Logger) -> None:
    """Generate an interactive Folium map from scored output."""
    try:
        import folium
    except ImportError:
        logger.error("folium not installed. Install with: pip install folium")
        return

    output = config.paths.output
    processed = config.paths.processed
    scored_path = output / "scored_cells.gpkg"

    if not scored_path.exists():
        logger.error("Scored cells not found at %s. Run 'score' first.", scored_path)
        return

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
        ("Excellent (80-100)", lambda s: s is not None and s >= 80, True),
        ("Good (60-79)", lambda s: s is not None and 60 <= s < 80, False),
        ("Fair (40-59)", lambda s: s is not None and 40 <= s < 60, False),
        ("Poor (20-39)", lambda s: s is not None and 20 <= s < 40, False),
        ("Unsuitable (0-19)", lambda s: s is not None and s < 20, False),
        ("Excluded", lambda s: s is None, False),
    ]

    # Columns to include in GeoJSON (drop heavy/unnecessary ones)
    keep_cols = ["geometry", "status", "score", "rank", "score_hydro",
                 "score_solar", "score_elevation", "score_access",
                 "score_buildable", "confidence", "confidence_band"]
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
        <i style="background:#1a9641;width:16px;height:16px;display:inline-block;margin:3px 6px 0 0;vertical-align:middle;border:1px solid #999;"></i> 80–100 (Excellent)<br>
        <i style="background:#a6d96a;width:16px;height:16px;display:inline-block;margin:3px 6px 0 0;vertical-align:middle;border:1px solid #999;"></i> 60–79 (Good)<br>
        <i style="background:#ffffbf;width:16px;height:16px;display:inline-block;margin:3px 6px 0 0;vertical-align:middle;border:1px solid #999;"></i> 40–59 (Fair)<br>
        <i style="background:#fdae61;width:16px;height:16px;display:inline-block;margin:3px 6px 0 0;vertical-align:middle;border:1px solid #999;"></i> 20–39 (Poor)<br>
        <i style="background:#d7191c;width:16px;height:16px;display:inline-block;margin:3px 6px 0 0;vertical-align:middle;border:1px solid #999;"></i> 0–19 (Unsuitable)<br>
        <i style="background:#808080;width:16px;height:16px;display:inline-block;margin:3px 6px 0 0;vertical-align:middle;border:1px solid #999;"></i> Excluded<br>
        <hr style="margin: 6px 0;">
        <b style="font-size: 13px;">Overlays</b><br>
        <span style="color:#2166ac;">&#9473;&#9473;</span> Streams & Rivers<br>
        <span style="color:#e31a1c;">&#9473;</span><span style="color:#fd8d3c;">&#9473;</span><span style="color:#888;">&#9473;</span> Roads (major → minor)<br>
        <i style="background:#9467bd;opacity:0.4;width:16px;height:16px;display:inline-block;margin:3px 6px 0 0;vertical-align:middle;border:1px solid #7b4ea3;"></i> Crown Land<br>
        <i style="background:#d73027;opacity:0.3;width:16px;height:16px;display:inline-block;margin:3px 6px 0 0;vertical-align:middle;border:1px dashed #d73027;"></i> Protected Areas<br>
        <i style="background:#555;width:16px;height:16px;display:inline-block;margin:3px 6px 0 0;vertical-align:middle;border:1px solid #333;"></i> Buildings<br>
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
    formula_html = """
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
            <tr style="border-bottom:1px solid #eee;">
                <td style="padding:4px 0;"><b style="color:#2166ac;">Micro-Hydro</b></td>
                <td style="padding:4px 8px; text-align:right; white-space:nowrap;"><b>45%</b></td>
                <td style="padding:4px 0; color:#555;">Nearby stream, flow rate, head</td>
            </tr>
            <tr style="border-bottom:1px solid #eee;">
                <td style="padding:4px 0;"><b style="color:#6B4226;">Elevation</b></td>
                <td style="padding:4px 8px; text-align:right;"><b>25%</b></td>
                <td style="padding:4px 0; color:#555;">30&ndash;100m ideal; coastal flood risk</td>
            </tr>
            <tr style="border-bottom:1px solid #eee;">
                <td style="padding:4px 0;"><b style="color:#e31a1c;">Access</b></td>
                <td style="padding:4px 8px; text-align:right;"><b>20%</b></td>
                <td style="padding:4px 0; color:#555;">Distance to nearest road</td>
            </tr>
            <tr style="border-bottom:1px solid #eee;">
                <td style="padding:4px 0;"><b style="color:#f4a020;">Solar</b></td>
                <td style="padding:4px 8px; text-align:right;"><b>5%</b></td>
                <td style="padding:4px 0; color:#555;">South-facing slopes, flat terrain</td>
            </tr>
            <tr>
                <td style="padding:4px 0;"><b style="color:#4a9e4a;">Buildable</b></td>
                <td style="padding:4px 8px; text-align:right;"><b>5%</b></td>
                <td style="padding:4px 0; color:#555;">Buildable land area</td>
            </tr>
        </table>
        <hr style="margin: 6px 0;">
        <span style="font-size: 11px; color: #888;">
            Each criterion scores 0&ndash;100, then weighted.<br>
            Cells in protected areas or flood zones are excluded.
        </span>
    </div>
    """
    m.get_root().html.add_child(folium.Element(formula_html))

    # Save map
    map_path = output / "map.html"
    m.save(str(map_path))
    logger.info("Saved interactive map: %s", map_path)

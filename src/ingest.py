"""Raw data ingestion — converts downloaded data into pipeline-ready formats.

Handles format differences: GDB layers, OSM PBF, compound CRS, nested paths.
Outputs standardized GPKG/GeoTIFF files in the processed directory.
"""

import logging
from pathlib import Path

import geopandas as gpd
import rasterio
from rasterio.warp import calculate_default_transform, reproject, Resampling
from shapely.geometry import box

from src.config import Config
from src.constants import WORKING_CRS

logger = logging.getLogger("property_finder")


def _bbox_to_geodataframe(bbox: tuple, crs: str) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(geometry=[box(*bbox)], crs=crs)


def _reproject_to_working(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Reproject a GeoDataFrame to the working CRS, handling compound CRS."""
    if gdf.crs is None:
        raise ValueError("GeoDataFrame has no CRS")
    try:
        epsg = gdf.crs.to_epsg()
        if epsg == 2961:
            return gdf
    except Exception:
        pass
    # Compound CRS (e.g., NAD83(CSRS)v6 + CGVD2013) — extract horizontal
    return gdf.to_crs(WORKING_CRS)


def ingest_dem(config: Config) -> Path | None:
    """Find and reproject the DEM to the working CRS, clipped to study area.

    Prefers HRDEM (2-5m LiDAR) over CDEM (18m). HRDEM gives much better
    head estimation for hydro scoring.
    """
    raw = config.paths.raw
    processed = config.paths.processed
    out_path = processed / "dem.tif"

    if out_path.exists():
        logger.info("DEM already processed: %s", out_path)
        return out_path

    # Prefer HRDEM (higher resolution) over CDEM
    hrdem_candidates = list((raw / "hrdem").glob("*.tif"))
    cdem_candidates = list((raw / "dem").glob("cdem_*.tif")) + list((raw / "dem").glob("*.tif"))

    if hrdem_candidates:
        src_path = hrdem_candidates[0]
        logger.info("Ingesting HRDEM (high-res LiDAR) from %s", src_path)
    elif cdem_candidates:
        src_path = cdem_candidates[0]
        logger.info("Ingesting CDEM (18m) from %s", src_path)
    else:
        logger.warning("No DEM raster found in %s or %s", raw / "hrdem", raw / "dem")
        return None

    bbox = config.study_area.bbox
    bbox_geom = box(*bbox)

    with rasterio.open(src_path) as src:
        # Reproject to working CRS
        transform, width, height = calculate_default_transform(
            src.crs, WORKING_CRS, src.width, src.height, *src.bounds
        )
        kwargs = src.meta.copy()
        kwargs.update(crs=WORKING_CRS, transform=transform, width=width, height=height)

        # Reproject full raster first
        reproj_path = processed / "dem_reproj.tif"
        reproj_path.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(reproj_path, "w", **kwargs) as dst:
            for i in range(1, src.count + 1):
                reproject(
                    source=rasterio.band(src, i),
                    destination=rasterio.band(dst, i),
                    src_transform=src.transform,
                    src_crs=src.crs,
                    dst_transform=transform,
                    dst_crs=WORKING_CRS,
                    resampling=Resampling.bilinear,
                )

    # Clip to study area
    from rasterio.mask import mask as rasterio_mask
    with rasterio.open(reproj_path) as src:
        out_image, out_transform = rasterio_mask(src, [bbox_geom], crop=True)
        out_meta = src.meta.copy()
        out_meta.update(
            height=out_image.shape[1],
            width=out_image.shape[2],
            transform=out_transform,
        )

    out_meta.update(compress="lzw")
    with rasterio.open(out_path, "w", **out_meta) as dst:
        dst.write(out_image)

    logger.info("DEM processed: %s (%dx%d)", out_path, out_meta["width"], out_meta["height"])
    # Clean up intermediate
    reproj_path.unlink(missing_ok=True)
    return out_path


def ingest_streams(config: Config) -> Path | None:
    """Ingest NSHN stream network from GDB or shapefile."""
    raw = config.paths.raw
    processed = config.paths.processed
    out_path = processed / "streams.gpkg"

    if out_path.exists():
        logger.info("Streams already processed: %s", out_path)
        return out_path

    # Try GDB first (NSHN)
    gdb_candidates = list((raw / "hydro").glob("*.gdb"))
    if gdb_candidates:
        gdb_path = gdb_candidates[0]
        logger.info("Ingesting streams from GDB: %s (layer: nshn_v2_wa_line)", gdb_path)
        try:
            gdf = gpd.read_file(gdb_path, layer="nshn_v2_wa_line")
        except Exception:
            # Try reading any line layer
            import fiona
            layers = fiona.listlayers(str(gdb_path))
            line_layers = [l for l in layers if "line" in l.lower() and "wa" in l.lower()]
            if line_layers:
                gdf = gpd.read_file(gdb_path, layer=line_layers[0])
            else:
                logger.warning("No suitable stream line layer found in %s", gdb_path)
                return None
    else:
        # Try shapefile
        shp_candidates = list((raw / "hydro").glob("*.shp"))
        if not shp_candidates:
            logger.warning("No stream data found in %s", raw / "hydro")
            return None
        gdf = gpd.read_file(shp_candidates[0])

    gdf = _reproject_to_working(gdf)

    # Clip to study area
    bbox_geom = box(*config.study_area.bbox)
    gdf = gdf[gdf.intersects(bbox_geom)].copy()
    gdf = gpd.clip(gdf, bbox_geom)

    processed.mkdir(parents=True, exist_ok=True)
    gdf.to_file(out_path, driver="GPKG")
    logger.info("Streams processed: %s (%d features)", out_path, len(gdf))
    return out_path


def ingest_roads_from_osm(config: Config) -> Path | None:
    """Ingest road network from OSM PBF file."""
    raw = config.paths.raw
    processed = config.paths.processed
    out_path = processed / "roads.gpkg"

    if out_path.exists():
        logger.info("Roads already processed: %s", out_path)
        return out_path

    pbf_candidates = list((raw / "roads").glob("*.osm.pbf"))
    if not pbf_candidates:
        # Try shapefile/gpkg fallback
        for ext in ("*.gpkg", "*.shp"):
            candidates = list((raw / "roads").glob(ext))
            if candidates:
                gdf = gpd.read_file(candidates[0])
                gdf = _reproject_to_working(gdf)
                bbox_geom = box(*config.study_area.bbox)
                gdf = gdf[gdf.intersects(bbox_geom)].copy()
                processed.mkdir(parents=True, exist_ok=True)
                gdf.to_file(out_path, driver="GPKG")
                logger.info("Roads processed from shapefile: %s (%d features)", out_path, len(gdf))
                return out_path
        logger.warning("No road data found in %s", raw / "roads")
        return None

    pbf_path = pbf_candidates[0]
    logger.info("Ingesting roads from OSM PBF: %s", pbf_path)

    # Read lines layer from OSM PBF — filter to road types
    gdf = gpd.read_file(pbf_path, layer="lines")

    # Filter to roads only (highway tag is not null)
    road_mask = gdf["highway"].notna()
    gdf = gdf[road_mask].copy()

    gdf = gdf.to_crs(WORKING_CRS)

    # Clip to study area
    bbox_geom = box(*config.study_area.bbox)
    gdf = gdf[gdf.intersects(bbox_geom)].copy()
    gdf = gpd.clip(gdf, bbox_geom)

    processed.mkdir(parents=True, exist_ok=True)
    gdf.to_file(out_path, driver="GPKG")
    logger.info("Roads processed: %s (%d features)", out_path, len(gdf))
    return out_path


def ingest_buildings(config: Config) -> Path | None:
    """Ingest building footprints from GPKG or shapefile."""
    raw = config.paths.raw
    processed = config.paths.processed
    out_path = processed / "buildings.gpkg"

    if out_path.exists():
        logger.info("Buildings already processed: %s", out_path)
        return out_path

    # Search recursively for GPKG files in buildings dir
    gpkg_candidates = list((raw / "buildings").rglob("*.gpkg"))
    shp_candidates = list((raw / "buildings").rglob("*.shp"))

    if not gpkg_candidates and not shp_candidates:
        logger.warning("No building data found in %s", raw / "buildings")
        return None

    src_file = gpkg_candidates[0] if gpkg_candidates else shp_candidates[0]
    logger.info("Ingesting buildings from %s", src_file)

    gdf = gpd.read_file(src_file)
    gdf = _reproject_to_working(gdf)

    # Clip to study area
    bbox_geom = box(*config.study_area.bbox)
    gdf = gdf[gdf.intersects(bbox_geom)].copy()

    processed.mkdir(parents=True, exist_ok=True)
    gdf.to_file(out_path, driver="GPKG")
    logger.info("Buildings processed: %s (%d features)", out_path, len(gdf))
    return out_path


def ingest_land_cover(config: Config) -> Path | None:
    """Ingest NSTDB land cover polygons."""
    raw = config.paths.raw
    processed = config.paths.processed
    out_path = processed / "land_cover.gpkg"

    if out_path.exists():
        logger.info("Land cover already processed: %s", out_path)
        return out_path

    # Look for polygon shapefile
    candidates = list((raw / "land-cover").glob("*POLY*.shp")) + \
                 list((raw / "land-cover").glob("*.shp"))
    if not candidates:
        logger.warning("No land cover data found in %s", raw / "land-cover")
        return None

    src_file = candidates[0]
    logger.info("Ingesting land cover from %s", src_file)

    gdf = gpd.read_file(src_file)
    gdf = _reproject_to_working(gdf)

    # Clip to study area
    bbox_geom = box(*config.study_area.bbox)
    gdf = gdf[gdf.intersects(bbox_geom)].copy()

    processed.mkdir(parents=True, exist_ok=True)
    gdf.to_file(out_path, driver="GPKG")
    logger.info("Land cover processed: %s (%d features)", out_path, len(gdf))
    return out_path


def ingest_crown_land(config: Config) -> Path | None:
    """Ingest Crown land parcels."""
    raw = config.paths.raw
    processed = config.paths.processed
    out_path = processed / "crown_land.gpkg"

    if out_path.exists():
        logger.info("Crown land already processed: %s", out_path)
        return out_path

    candidates = list((raw / "crown-land").glob("*.shp"))
    if not candidates:
        logger.warning("No Crown land data found in %s", raw / "crown-land")
        return None

    src_file = candidates[0]
    logger.info("Ingesting Crown land from %s", src_file)

    gdf = gpd.read_file(src_file)
    gdf = _reproject_to_working(gdf)

    # Clip to study area
    bbox_geom = box(*config.study_area.bbox)
    gdf = gdf[gdf.intersects(bbox_geom)].copy()

    processed.mkdir(parents=True, exist_ok=True)
    gdf.to_file(out_path, driver="GPKG")
    logger.info("Crown land processed: %s (%d features)", out_path, len(gdf))
    return out_path


def ingest_protected_areas(config: Config) -> Path | None:
    """Ingest Protected Areas from NS Open Data shapefile."""
    raw = config.paths.raw
    processed = config.paths.processed
    out_path = processed / "protected_areas.gpkg"

    if out_path.exists():
        logger.info("Protected areas already processed: %s", out_path)
        return out_path

    # Look for shapefile in exclusions directory (may be in subdirectory)
    shp_candidates = list((raw / "exclusions").rglob("*.shp"))
    if not shp_candidates:
        logger.warning("No protected areas data found in %s", raw / "exclusions")
        return None

    src_file = shp_candidates[0]
    logger.info("Ingesting protected areas from %s", src_file)

    gdf = gpd.read_file(src_file)
    gdf = _reproject_to_working(gdf)

    # Clip to study area
    bbox_geom = box(*config.study_area.bbox)
    gdf = gdf[gdf.intersects(bbox_geom)].copy()

    if gdf.empty:
        logger.info("No protected areas within study area")
        return None

    processed.mkdir(parents=True, exist_ok=True)
    gdf.to_file(out_path, driver="GPKG")
    logger.info("Protected areas processed: %s (%d features)", out_path, len(gdf))
    return out_path


def _normalize_parcel_fields(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Rename source PID/AAN columns to canonical ``PID``/``AAN``.

    PIDs and AANs are identifiers, not numbers — leading zeros are
    significant — so they are coerced to stripped strings. The first matching
    source column wins; unmatched parcels keep an empty string.
    """
    from src.constants import PARCEL_AAN_FIELDS, PARCEL_PID_FIELDS

    gdf = gdf.copy()
    upper = {c.upper(): c for c in gdf.columns}

    def _first_match(candidates: tuple[str, ...]) -> str | None:
        for cand in candidates:
            if cand.upper() in upper:
                return upper[cand.upper()]
        return None

    pid_col = _first_match(PARCEL_PID_FIELDS)
    if pid_col is not None and pid_col != "PID":
        gdf = gdf.rename(columns={pid_col: "PID"})
    elif pid_col is None:
        logger.warning(
            "No PID-like column found in parcel data (columns: %s); "
            "PID will be blank", list(gdf.columns)
        )
        gdf["PID"] = ""

    aan_col = _first_match(PARCEL_AAN_FIELDS)
    if aan_col is not None and aan_col != "AAN":
        gdf = gdf.rename(columns={aan_col: "AAN"})

    # Identifiers as clean strings (preserve leading zeros, drop trailing ".0")
    for col in ("PID", "AAN"):
        if col in gdf.columns:
            gdf[col] = (
                gdf[col]
                .astype("string")
                .str.replace(r"\.0$", "", regex=True)
                .fillna("")
                .str.strip()
            )
    return gdf


def _build_nsprd_query_url(
    base_url: str, layer_id: int, bbox_4326: tuple, offset: int, count: int
) -> str:
    """Build an ArcGIS REST query URL for a bbox page (pure, testable)."""
    from urllib.parse import urlencode

    xmin, ymin, xmax, ymax = bbox_4326
    params = {
        "where": "1=1",
        "geometry": f"{xmin},{ymin},{xmax},{ymax}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "outSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "*",
        "returnGeometry": "true",
        "resultOffset": str(offset),
        "resultRecordCount": str(count),
        "f": "geojson",
    }
    return f"{base_url.rstrip('/')}/{layer_id}/query?{urlencode(params)}"


def _features_to_gdf(geojson: dict) -> gpd.GeoDataFrame:
    """Parse an ArcGIS GeoJSON FeatureCollection into a GeoDataFrame (4326)."""
    features = (geojson or {}).get("features", [])
    if not features:
        return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
    return gpd.GeoDataFrame.from_features(features, crs="EPSG:4326")


def fetch_nsprd_parcels(
    bbox_working: tuple,
    base_url: str | None = None,
    layer_id: int | None = None,
    page_size: int | None = None,
    timeout: int = 60,
) -> gpd.GeoDataFrame | None:
    """Fetch NSPRD parcel polygons (with PID) from the public ArcGIS REST service.

    Queries by the study-area bounding box, paginating until the server stops
    returning a full page. Network-dependent and best-effort: returns ``None``
    on any failure so the caller can fall back to a local file. Geometry is
    reprojected to the working CRS.
    """
    from src.constants import (
        NSPRD_PAGE_SIZE,
        NSPRD_PARCEL_LAYER_ID,
        NSPRD_PARCEL_SERVICE,
    )

    base_url = base_url or NSPRD_PARCEL_SERVICE
    layer_id = NSPRD_PARCEL_LAYER_ID if layer_id is None else layer_id
    page_size = page_size or NSPRD_PAGE_SIZE

    try:
        import requests
    except ImportError:
        logger.error("`requests` is required for --from-rest parcel ingestion")
        return None

    # Service expects the query envelope in lat/lon (inSR=4326).
    bbox_4326 = tuple(
        gpd.GeoSeries([box(*bbox_working)], crs=WORKING_CRS)
        .to_crs("EPSG:4326")
        .total_bounds
    )

    pages: list[gpd.GeoDataFrame] = []
    offset = 0
    try:
        while True:
            url = _build_nsprd_query_url(base_url, layer_id, bbox_4326, offset, page_size)
            resp = requests.get(url, timeout=timeout)
            resp.raise_for_status()
            payload = resp.json()
            page = _features_to_gdf(payload)
            if page.empty:
                break
            pages.append(page)
            logger.info("NSPRD: fetched %d parcels (offset %d)", len(page), offset)
            if len(page) < page_size and not payload.get("exceededTransferLimit"):
                break
            offset += page_size
    except Exception:
        logger.exception("NSPRD REST fetch failed; fall back to a local parcel file")
        return None

    if not pages:
        logger.warning("NSPRD REST returned no parcels for the study area")
        return None

    import pandas as pd

    combined = gpd.GeoDataFrame(
        pd.concat(pages, ignore_index=True), crs="EPSG:4326"
    )
    return combined.to_crs(WORKING_CRS)


def ingest_parcels(config: Config, source: str = "local") -> Path | None:
    """Ingest NS property parcels (with PID) into ``processed/parcels.gpkg``.

    ``source="local"`` reads a downloaded file from ``data/raw/parcels/``
    (GPKG, shapefile, GeoJSON, or File Geodatabase). ``source="rest"`` pulls
    directly from the public NSPRD ArcGIS service for the study-area bbox.

    Output carries canonical ``PID`` / ``AAN`` columns and ``area_acres``,
    reprojected to the working CRS and clipped to the study area.
    """
    raw = config.paths.raw
    processed = config.paths.processed
    out_path = processed / "parcels.gpkg"

    if out_path.exists():
        logger.info("Parcels already processed: %s", out_path)
        return out_path

    if source == "rest":
        logger.info("Fetching parcels from NSPRD REST service")
        gdf = fetch_nsprd_parcels(config.study_area.bbox)
        if gdf is None or gdf.empty:
            return None
    else:
        candidates = (
            list((raw / "parcels").rglob("*.gpkg"))
            + list((raw / "parcels").rglob("*.shp"))
            + list((raw / "parcels").rglob("*.geojson"))
            + list((raw / "parcels").rglob("*.gdb"))
        )
        if not candidates:
            logger.warning(
                "No parcel data found in %s — download NSPRD parcels (GeoNOVA) "
                "or run `ingest-parcels --from-rest`", raw / "parcels"
            )
            return None
        src_file = candidates[0]
        logger.info("Ingesting parcels from %s", src_file)
        gdf = gpd.read_file(src_file)
        gdf = _reproject_to_working(gdf)

    # Clip to study area
    bbox_geom = box(*config.study_area.bbox)
    gdf = gdf[gdf.intersects(bbox_geom)].copy()
    if gdf.empty:
        logger.warning("No parcels within study area")
        return None
    gdf = gpd.clip(gdf, bbox_geom)
    # Drop slivers / non-polygon leftovers from clipping
    gdf = gdf[gdf.geometry.geom_type.isin(("Polygon", "MultiPolygon"))].copy()

    gdf = _normalize_parcel_fields(gdf)
    gdf["area_acres"] = gdf.geometry.area / 4046.86

    processed.mkdir(parents=True, exist_ok=True)
    gdf.to_file(out_path, driver="GPKG")
    logger.info(
        "Parcels processed: %s (%d parcels, %d with PID)",
        out_path, len(gdf), int((gdf.get("PID", "") != "").sum()),
    )
    return out_path


def ingest_waterbodies(config: Config) -> Path | None:
    """Ingest waterbody polygons (lakes/ponds/ocean) for the buildability mask.

    Optional. Looks in ``data/raw/water/`` (or ``waterbodies/``) for a polygon
    layer — e.g. the NSTDB waterbody layer — and clips/reprojects it. Without
    this layer the mask falls back to a DEM sea-level proxy + watercourse lines,
    which cannot exclude lake/ocean interiors.
    """
    raw = config.paths.raw
    processed = config.paths.processed
    out_path = processed / "waterbodies.gpkg"

    if out_path.exists():
        logger.info("Waterbodies already processed: %s", out_path)
        return out_path

    candidates = (
        list((raw / "water").rglob("*.shp")) + list((raw / "water").rglob("*.gpkg"))
        + list((raw / "waterbodies").rglob("*.shp")) + list((raw / "waterbodies").rglob("*.gpkg"))
    )
    if not candidates:
        logger.info("No waterbody polygon layer found (optional); skipping")
        return None

    gdf = gpd.read_file(candidates[0])
    gdf = _reproject_to_working(gdf)
    bbox_geom = box(*config.study_area.bbox)
    gdf = gdf[gdf.intersects(bbox_geom)].copy()
    if gdf.empty:
        return None
    gdf = gdf[gdf.geometry.geom_type.isin(("Polygon", "MultiPolygon"))].copy()

    processed.mkdir(parents=True, exist_ok=True)
    gdf.to_file(out_path, driver="GPKG")
    logger.info("Waterbodies processed: %s (%d features)", out_path, len(gdf))
    return out_path


def run_ingest(config: Config, logger: logging.Logger) -> dict[str, Path | None]:
    """Run all ingestion steps. Returns dict of layer_name -> processed path."""
    processed = config.paths.processed
    processed.mkdir(parents=True, exist_ok=True)

    results = {}
    results["dem"] = ingest_dem(config)
    results["streams"] = ingest_streams(config)
    results["roads"] = ingest_roads_from_osm(config)
    results["buildings"] = ingest_buildings(config)
    results["land_cover"] = ingest_land_cover(config)
    results["crown_land"] = ingest_crown_land(config)
    results["protected_areas"] = ingest_protected_areas(config)
    results["waterbodies"] = ingest_waterbodies(config)
    results["parcels"] = ingest_parcels(config)

    logger.info("=== Ingestion summary ===")
    for name, path in results.items():
        status = f"OK ({path})" if path else "MISSING"
        logger.info("  %s: %s", name, status)

    return results

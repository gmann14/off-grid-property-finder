"""Raw data ingestion — converts downloaded data into pipeline-ready formats.

Handles format differences: GDB layers, OSM PBF, compound CRS, nested paths.
Outputs standardized GPKG/GeoTIFF files in the processed directory.
"""

import logging
import os
from contextlib import contextmanager
from pathlib import Path

import geopandas as gpd
import rasterio
from rasterio.warp import calculate_default_transform, reproject, Resampling
from shapely.geometry import box

from src.config import Config
from src.constants import WORKING_CRS

logger = logging.getLogger("property_finder")


@contextmanager
def _atomic_output(out_path: Path):
    """Yield a temp path beside ``out_path`` to write to; when the block
    finishes, the temp file replaces ``out_path`` in one rename. If the write
    fails (disk full, interrupted), the temp file is removed and ``out_path``
    is untouched, so a partial file can never be cached by the
    skip-if-exists checks."""
    tmp_path = out_path.with_name(f"{out_path.stem}.partial{out_path.suffix}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path.unlink(missing_ok=True)
    try:
        yield tmp_path
        os.replace(tmp_path, out_path)
    finally:
        tmp_path.unlink(missing_ok=True)


def _write_gpkg(gdf: gpd.GeoDataFrame, out_path: Path) -> None:
    """Write ``gdf`` to ``out_path`` as a GPKG via `_atomic_output`. The layer
    is named after ``out_path`` (as a direct write would name it), not the
    temp file."""
    with _atomic_output(out_path) as tmp_path:
        gdf.to_file(tmp_path, driver="GPKG", layer=out_path.stem)


def _bbox_to_geodataframe(bbox: tuple, crs: str) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(geometry=[box(*bbox)], crs=crs)


def _reproject_to_working(gdf: gpd.GeoDataFrame, working_crs: str = WORKING_CRS) -> gpd.GeoDataFrame:
    """Reproject a GeoDataFrame to the working CRS, handling compound CRS."""
    if gdf.crs is None:
        raise ValueError("GeoDataFrame has no CRS")
    try:
        epsg = gdf.crs.to_epsg()
        if epsg == int(working_crs.split(":")[1]):
            return gdf
    except Exception:
        pass
    # Compound CRS (e.g., NAD83(CSRS)v6 + CGVD2013) — extract horizontal
    return gdf.to_crs(working_crs)


def _transform_bounds(bbox: tuple, src_crs, dst_crs) -> tuple[float, float, float, float]:
    """``bbox`` (in ``src_crs``) as bounds in ``dst_crs``, from 21 points along
    each edge rather than the four corners, so an edge that curves outward in
    the target CRS (a UTM bbox spanning the central meridian, seen in lat/lon)
    stays inside the result."""
    from pyproj import CRS as ProjCRS, Transformer

    transformer = Transformer.from_crs(
        ProjCRS.from_user_input(src_crs), ProjCRS.from_user_input(dst_crs), always_xy=True
    )
    return tuple(float(v) for v in transformer.transform_bounds(*bbox, densify_pts=21))


# Nodata for a DEM mosaic whose tiles declare none (the HRDEM/CDEM value).
_DEM_DEFAULT_NODATA = -32767.0
# Source pixels of padding around the study area when mosaicking tiles, so the
# bilinear reprojection has neighbours at the edges.
_DEM_MOSAIC_PAD_PX = 2


def _dem_tiles(raw: Path) -> tuple[list[Path], list[Path]]:
    """HRDEM tiles (data/raw/hrdem/) and CDEM tiles (data/raw/dem/), sorted,
    each file once."""
    hrdem = sorted((raw / "hrdem").glob("*.tif"))
    cdem = list(dict.fromkeys(
        sorted((raw / "dem").glob("cdem_*.tif")) + sorted((raw / "dem").glob("*.tif"))
    ))
    return hrdem, cdem


def _mosaic_dem_tiles(tiles: list[Path], dst_path: Path, bbox: tuple, working_crs: str) -> Path:
    """One raster for a list of DEM tiles: a single tile is returned as is;
    several are mosaicked with rasterio.merge over the study area (padded) into
    ``dst_path``, at the finest tile resolution, the first tile in name order
    winning where tiles overlap. Tiles in different CRSs are warped (bilinear)
    to the working CRS first."""
    if len(tiles) == 1:
        return tiles[0]
    from contextlib import ExitStack

    from rasterio.merge import merge
    from rasterio.vrt import WarpedVRT

    with ExitStack() as stack:
        srcs = [stack.enter_context(rasterio.open(p)) for p in tiles]
        if len({s.crs for s in srcs}) > 1:
            srcs = [stack.enter_context(WarpedVRT(s, crs=working_crs, resampling=Resampling.bilinear))
                    for s in srcs]
        first = srcs[0]
        nodata = first.nodata if first.nodata is not None else _DEM_DEFAULT_NODATA
        pad = _DEM_MOSAIC_PAD_PX * max(abs(r) for s in srcs for r in s.res)
        b = _transform_bounds(bbox, working_crs, first.crs)
        bounds = (
            max(b[0] - pad, min(s.bounds.left for s in srcs)),
            max(b[1] - pad, min(s.bounds.bottom for s in srcs)),
            min(b[2] + pad, max(s.bounds.right for s in srcs)),
            min(b[3] + pad, max(s.bounds.top for s in srcs)),
        )
        if bounds[0] >= bounds[2] or bounds[1] >= bounds[3]:
            raise ValueError(f"None of the DEM tiles {[p.name for p in tiles]} overlap the study area {bbox}")
        dst_path.unlink(missing_ok=True)
        # The output profile starts from the first source's, so set the driver
        # and block layout explicitly (a WarpedVRT source would say "VRT").
        merge(srcs, bounds=bounds, nodata=nodata, use_highest_res=True, dst_path=dst_path,
              dst_kwds={"driver": "GTiff", "tiled": True, "blockxsize": 256, "blockysize": 256,
                        "BIGTIFF": "IF_SAFER"})
    logger.info("Mosaicked %d DEM tiles: %s", len(tiles), ", ".join(p.name for p in tiles))
    return dst_path


def _extend_dem_to_bbox(image, transform, nodata, bbox):
    """Place a cropped DEM on a grid aligned with it that covers the whole
    bbox (never smaller than the input). Returns ``(image, transform, inside,
    have)``: ``inside`` marks pixels whose centre is in the bbox (what
    rasterio.mask keeps), ``have`` pixels holding a DEM value. When the input
    already covers the bbox the image is returned as is, not copied."""
    import math

    import numpy as np
    from rasterio.features import geometry_mask
    from rasterio.transform import from_origin

    _, h, w = image.shape
    rx, ry = transform.a, -transform.e
    x0, y0 = transform.c, transform.f
    xmin, ymin, xmax, ymax = bbox
    eps = 1e-6
    col0 = min(0, math.floor((xmin - x0) / rx + eps))
    col1 = max(w, math.ceil((xmax - x0) / rx - eps))
    row0 = min(0, math.floor((y0 - ymax) / ry + eps))
    row1 = max(h, math.ceil((y0 - ymin) / ry - eps))
    if (col0, row0, col1, row1) == (0, 0, w, h):
        ext, ext_transform = image, transform
    else:
        fill = nodata if nodata is not None else _DEM_DEFAULT_NODATA
        ext = np.full((image.shape[0], row1 - row0, col1 - col0), fill, dtype=image.dtype)
        ext[:, -row0:-row0 + h, -col0:-col0 + w] = image
        ext_transform = from_origin(x0 + col0 * rx, y0 - row0 * ry, rx, ry)
    inside = geometry_mask([box(*bbox)], out_shape=ext.shape[1:], transform=ext_transform, invert=True)
    if nodata is None:
        have = np.zeros(ext.shape[1:], dtype=bool)
        have[-row0:-row0 + h, -col0:-col0 + w] = True
    else:
        band = ext[0]
        have = np.isfinite(band)
        if not np.isnan(nodata):
            have &= band != nodata
    return ext, ext_transform, inside, have


def _fill_dem_from(ext, ext_transform, nodata, inside, have, fill_path: Path, working_crs: str) -> int:
    """Fill ``ext`` (in place) where it has no value inside the bbox from the
    raster at ``fill_path``, reprojected (bilinear) onto its grid. Returns the
    number of pixels filled."""
    import numpy as np

    filled = np.zeros(ext.shape[1:], dtype=bool)
    with rasterio.open(fill_path) as fsrc:
        for i in range(ext.shape[0]):
            fill = np.full(ext.shape[1:], nodata, dtype=ext.dtype)
            reproject(
                source=rasterio.band(fsrc, min(i + 1, fsrc.count)), destination=fill,
                src_transform=fsrc.transform, src_crs=fsrc.crs, src_nodata=fsrc.nodata,
                dst_transform=ext_transform, dst_crs=working_crs, dst_nodata=nodata,
                resampling=Resampling.bilinear,
            )
            take = inside & ~have & np.isfinite(fill) & (fill != nodata)
            ext[i][take] = fill[take]
            filled |= take
    return int(filled.sum())


def ingest_dem(config: Config) -> Path | None:
    """Find and reproject the DEM to the working CRS, clipped to study area.

    Prefers HRDEM (2-5m LiDAR) over CDEM (18m). HRDEM gives much better
    head estimation for hydro scoring. Every tile of the chosen source is used
    (mosaicked when there are several). Where HRDEM leaves part of the study
    area without data, that part is filled from CDEM when CDEM tiles are
    present; any part still without data is reported with the covered share.
    """
    raw = config.paths.raw
    processed = config.paths.processed
    working_crs = config.working_crs
    out_path = processed / "dem.tif"

    if out_path.exists():
        logger.info("DEM already processed: %s", out_path)
        return out_path

    # Prefer HRDEM (higher resolution) over CDEM
    hrdem_tiles, cdem_tiles = _dem_tiles(raw)
    no_fill_hint = ""
    if hrdem_tiles:
        tiles, label, fill_tiles = hrdem_tiles, "HRDEM (high-res LiDAR)", cdem_tiles
        no_fill_hint = f"; add CDEM tiles to {raw / 'dem'} to fill the gap"
    elif cdem_tiles:
        tiles, label, fill_tiles = cdem_tiles, "CDEM (18m)", []
    else:
        logger.warning("No DEM raster found in %s or %s", raw / "hrdem", raw / "dem")
        return None
    if len(tiles) == 1:
        logger.info("Ingesting %s from %s", label, tiles[0])
    else:
        logger.info("Ingesting %s from %d tiles in %s", label, len(tiles), tiles[0].parent)

    bbox = config.study_area.bbox
    processed.mkdir(parents=True, exist_ok=True)
    mosaic_path = processed / "dem_mosaic.tif"
    fill_mosaic_path = processed / "dem_fill_mosaic.tif"
    try:
        src_path = _mosaic_dem_tiles(tiles, mosaic_path, bbox, working_crs)
        out_image, out_meta = _reproject_clip_dem(src_path, processed, bbox, working_crs)
        mosaic_path.unlink(missing_ok=True)
        out_image, out_meta = _check_dem_coverage(
            out_image, out_meta, bbox, working_crs, label, fill_tiles, fill_mosaic_path,
            "" if fill_tiles else no_fill_hint,
        )
    finally:
        mosaic_path.unlink(missing_ok=True)
        fill_mosaic_path.unlink(missing_ok=True)

    out_meta.update(compress="lzw")
    with _atomic_output(out_path) as tmp_path:
        with rasterio.open(tmp_path, "w", **out_meta) as dst:
            dst.write(out_image)

    logger.info("DEM processed: %s (%dx%d)", out_path, out_meta["width"], out_meta["height"])
    return out_path


def _reproject_clip_dem(src_path: Path, processed: Path, bbox: tuple, working_crs: str):
    """Reproject ``src_path`` (bilinear) to the working CRS and crop it to the
    bbox. Returns ``(image, meta)``."""
    bbox_geom = box(*bbox)
    reproj_path = processed / "dem_reproj.tif"
    try:
        with rasterio.open(src_path) as src:
            # Reproject to working CRS
            transform, width, height = calculate_default_transform(
                src.crs, working_crs, src.width, src.height, *src.bounds
            )
            kwargs = src.meta.copy()
            kwargs.update(crs=working_crs, transform=transform, width=width, height=height)

            # Reproject full raster first
            reproj_path.parent.mkdir(parents=True, exist_ok=True)
            with rasterio.open(reproj_path, "w", **kwargs) as dst:
                for i in range(1, src.count + 1):
                    reproject(
                        source=rasterio.band(src, i),
                        destination=rasterio.band(dst, i),
                        src_transform=src.transform,
                        src_crs=src.crs,
                        dst_transform=transform,
                        dst_crs=working_crs,
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
    finally:
        # Clean up intermediate, also when the reprojection or clip fails
        reproj_path.unlink(missing_ok=True)
    return out_image, out_meta


def _check_dem_coverage(image, meta, bbox, working_crs, label, fill_tiles, fill_mosaic_path,
                        no_fill_hint: str = ""):
    """Measure how much of the bbox the cropped DEM has values for; fill any
    gap from ``fill_tiles`` (CDEM under HRDEM) and warn about what is left.
    Returns ``(image, meta)``, unchanged unless a gap was filled."""
    nodata = meta["nodata"]
    ext, ext_transform, inside, have = _extend_dem_to_bbox(image, meta["transform"], nodata, bbox)
    n_inside = int(inside.sum())
    n_have = int((inside & have).sum())
    if n_inside == 0 or n_have == n_inside:
        return image, meta

    share = n_have / n_inside
    if fill_tiles:
        fill_nodata = nodata if nodata is not None else _DEM_DEFAULT_NODATA
        fill_src = _mosaic_dem_tiles(fill_tiles, fill_mosaic_path, bbox, working_crs)
        n_filled = _fill_dem_from(ext, ext_transform, fill_nodata, inside, have, fill_src, working_crs)
        ext[:, ~inside] = fill_nodata
        logger.info(
            "%s has values for %.1f%% of the study area; filled %d px (%.1f%%) from CDEM (%s)",
            label, 100 * share, n_filled, 100 * n_filled / n_inside,
            ", ".join(p.name for p in fill_tiles),
        )
        image = ext
        meta = {**meta, "nodata": fill_nodata, "transform": ext_transform,
                "height": ext.shape[1], "width": ext.shape[2]}
        n_have += n_filled
        share = n_have / n_inside
    if n_have < n_inside:
        logger.warning(
            "DEM has values for only %.1f%% of the study area (%d of %d px have no data); "
            "cells there get no elevation, slope or hydro head%s",
            100 * share, n_inside - n_have, n_inside, no_fill_hint,
        )
    return image, meta


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

    gdf = _reproject_to_working(gdf, config.working_crs)

    # Clip to study area
    bbox_geom = box(*config.study_area.bbox)
    gdf = gdf[gdf.intersects(bbox_geom)].copy()
    gdf = gpd.clip(gdf, bbox_geom)

    processed.mkdir(parents=True, exist_ok=True)
    _write_gpkg(gdf, out_path)
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
                gdf = _reproject_to_working(gdf, config.working_crs)
                bbox_geom = box(*config.study_area.bbox)
                gdf = gdf[gdf.intersects(bbox_geom)].copy()
                processed.mkdir(parents=True, exist_ok=True)
                _write_gpkg(gdf, out_path)
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

    gdf = gdf.to_crs(config.working_crs)

    # Clip to study area
    bbox_geom = box(*config.study_area.bbox)
    gdf = gdf[gdf.intersects(bbox_geom)].copy()
    gdf = gpd.clip(gdf, bbox_geom)

    processed.mkdir(parents=True, exist_ok=True)
    _write_gpkg(gdf, out_path)
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
    gdf = _reproject_to_working(gdf, config.working_crs)

    # Clip to study area
    bbox_geom = box(*config.study_area.bbox)
    gdf = gdf[gdf.intersects(bbox_geom)].copy()

    processed.mkdir(parents=True, exist_ok=True)
    _write_gpkg(gdf, out_path)
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
    gdf = _reproject_to_working(gdf, config.working_crs)

    # Clip to study area
    bbox_geom = box(*config.study_area.bbox)
    gdf = gdf[gdf.intersects(bbox_geom)].copy()

    processed.mkdir(parents=True, exist_ok=True)
    _write_gpkg(gdf, out_path)
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
    gdf = _reproject_to_working(gdf, config.working_crs)

    # Clip to study area
    bbox_geom = box(*config.study_area.bbox)
    gdf = gdf[gdf.intersects(bbox_geom)].copy()

    processed.mkdir(parents=True, exist_ok=True)
    _write_gpkg(gdf, out_path)
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
    gdf = _reproject_to_working(gdf, config.working_crs)

    # Clip to study area
    bbox_geom = box(*config.study_area.bbox)
    gdf = gdf[gdf.intersects(bbox_geom)].copy()

    if gdf.empty:
        logger.info("No protected areas within study area")
        return None

    processed.mkdir(parents=True, exist_ok=True)
    _write_gpkg(gdf, out_path)
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
    # NS PIDs are 8 digits; if a numeric driver dropped the leading zero before
    # we stringified, re-pad all-digit PIDs (1-7 chars) back to width 8.
    if "PID" in gdf.columns:
        digit_pid = gdf["PID"].str.fullmatch(r"\d{1,7}").fillna(False)
        gdf.loc[digit_pid, "PID"] = gdf.loc[digit_pid, "PID"].str.zfill(8)
    return gdf


def _dissolve_by_pid(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Merge multiple polygon records for the same PID into one row.

    NSPRD can serve more than one polygon per PID. Without this, one property
    occupies multiple ranked rows, each with fragment-level acreage. Rows with
    a blank PID are left as-is — they can't be reliably deduplicated. This
    does not recover the part of a parcel outside the study bbox: ingest_parcels
    takes that acreage from the unclipped geometry (`_whole_area_m2_if_cut`).
    """
    import pandas as pd

    if "PID" not in gdf.columns:
        return gdf
    has_pid = gdf["PID"].astype(bool)
    if not has_pid.any():
        return gdf

    n_before = int(has_pid.sum())
    with_pid = gdf[has_pid].dissolve(by="PID", as_index=False, aggfunc="first")
    without_pid = gdf[~has_pid]
    merged = gpd.GeoDataFrame(
        pd.concat([with_pid, without_pid], ignore_index=True), crs=gdf.crs
    )
    n_after = int((merged["PID"].astype(bool)).sum())
    if n_after < n_before:
        logger.info("Dissolved %d fragment(s) into %d unique PIDs", n_before, n_after)
    return merged


def _whole_area_m2_if_cut(gdf: gpd.GeoDataFrame, bbox_geom) -> "pd.Series":
    """The whole mapped area (m²) of each parcel the study bbox cuts, else NaN.

    Run on the unclipped parcels (after `_normalize_parcel_fields`). A parcel
    is cut when any of its records is not wholly inside the bbox: it crosses
    the edge, or has a part outside it. For a cut PID every one of its records
    gets the area of the union of all of them (overlapping records count
    once); a cut record with no PID gets its own area. Parcels wholly inside
    get NaN, so their acreage keeps coming from the clipped geometry exactly
    as before.
    """
    import numpy as np
    import pandas as pd
    import shapely

    out = pd.Series(np.nan, index=gdf.index, dtype="float64")
    if gdf.empty:
        return out
    geoms = gdf.geometry.values
    intersects = shapely.intersects(geoms, bbox_geom)
    within = shapely.within(geoms, bbox_geom)
    has_pid = gdf["PID"].astype(bool).to_numpy()
    pids = gdf["PID"].to_numpy()

    # PIDs in the study area with a record not wholly inside it
    cut_pids = set(pids[has_pid & ~within]) & set(pids[has_pid & intersects])
    if cut_pids:
        sel = np.flatnonzero(has_pid & np.isin(pids, list(cut_pids)))
        by_pid: dict[str, list[int]] = {}
        for i in sel:
            by_pid.setdefault(pids[i], []).append(i)
        for rows in by_pid.values():
            parts = geoms[rows]
            area = shapely.area(parts[0]) if len(rows) == 1 else shapely.area(shapely.union_all(parts))
            out.iloc[rows] = float(area)
    blank_cut = ~has_pid & intersects & ~within
    out.iloc[np.flatnonzero(blank_cut)] = shapely.area(geoms[blank_cut])
    return out


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
        # Stable ordering is required for resultOffset paging — without it the
        # server's row order is unspecified across requests, so pages can overlap
        # or drop records.
        "orderByFields": "OBJECTID",
        "resultOffset": str(offset),
        "resultRecordCount": str(count),
        "f": "geojson",
    }
    return f"{base_url.rstrip('/')}/{layer_id}/query?{urlencode(params)}"


def _exceeded_transfer_limit(payload: dict) -> bool:
    """Whether an ArcGIS query response says more records are left: the flag
    sits at the top level for f=json and under "properties" for f=geojson."""
    if not isinstance(payload, dict):
        return False
    props = payload.get("properties")
    return bool(payload.get("exceededTransferLimit")
                or (isinstance(props, dict) and props.get("exceededTransferLimit")))


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
    working_crs: str = WORKING_CRS,
) -> gpd.GeoDataFrame | None:
    """Fetch NSPRD parcel polygons (with PID) from the public ArcGIS REST service.

    Queries by the study-area bounding box, paginating by the rows each page
    returns until a page comes back short without exceededTransferLimit, or
    empty. Network-dependent and best-effort: returns ``None``
    on any failure so the caller can fall back to a local file. Geometry is
    reprojected to ``working_crs``.
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
        gpd.GeoSeries([box(*bbox_working)], crs=working_crs)
        .to_crs("EPSG:4326")
        .total_bounds
    )

    # Hard cap so a MapServer that ignores resultOffset (no pagination support)
    # can't loop forever returning the same first page.
    max_offset = 1_000_000
    pages: list[gpd.GeoDataFrame] = []
    offset = 0
    try:
        while offset <= max_offset:
            url = _build_nsprd_query_url(base_url, layer_id, bbox_4326, offset, page_size)
            resp = requests.get(url, timeout=timeout)
            resp.raise_for_status()
            payload = resp.json()
            # ArcGIS servers can return HTTP 200 with an error body (throttling,
            # timeout, bad params). Treating that like an empty/end-of-data page
            # would silently return a truncated (but seemingly complete) result —
            # abort the whole fetch instead so a partial parcel set never gets
            # written and cached as if it were the full study area.
            if isinstance(payload, dict) and payload.get("error"):
                logger.error(
                    "NSPRD REST returned an error at offset %d: %s",
                    offset, payload["error"].get("message", payload["error"]),
                )
                return None
            page = _features_to_gdf(payload)
            if page.empty:
                break
            pages.append(page)
            logger.info("NSPRD: fetched %d parcels (offset %d)", len(page), offset)
            # A server may cap a page below the size asked for (its
            # maxRecordCount) and say so with exceededTransferLimit, which
            # f=geojson puts under "properties". Page on by the rows actually
            # returned, so a capped page doesn't skip the rows after it.
            if len(page) < page_size and not _exceeded_transfer_limit(payload):
                break
            offset += len(page)
        else:
            logger.warning("NSPRD paging hit the %d-record cap; results may be truncated "
                           "(server may not support resultOffset paging)", max_offset)
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
    return combined.to_crs(working_crs)


def ingest_parcels(
    config: Config,
    source: str = "local",
    layer_id: int | None = None,
    base_url: str | None = None,
) -> Path | None:
    """Ingest NS property parcels (with PID) into ``processed/parcels.gpkg``.

    ``source="local"`` reads a downloaded file from ``data/raw/parcels/``
    (GPKG, shapefile, GeoJSON, or File Geodatabase). ``source="rest"`` pulls
    directly from the NSPRD ArcGIS service for the study-area bbox; ``layer_id``
    / ``base_url`` override the defaults if the parcel polygons live on a
    different layer/service.

    Output carries canonical ``PID`` / ``AAN`` columns and ``area_acres``,
    reprojected to the working CRS and clipped to the study area.
    ``area_acres`` is the parcel's whole mapped area, measured before the clip:
    a parcel crossing the study-area edge keeps its full acreage, while its
    geometry is only the part inside.
    """
    from src.constants import SQ_M_PER_ACRE

    raw = config.paths.raw
    processed = config.paths.processed
    out_path = processed / "parcels.gpkg"

    if out_path.exists():
        logger.info("Parcels already processed: %s", out_path)
        return out_path

    if source == "rest":
        logger.info("Fetching parcels from NSPRD REST service")
        gdf = fetch_nsprd_parcels(
            config.study_area.bbox, base_url=base_url, layer_id=layer_id,
            working_crs=config.working_crs,
        )
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
        gdf = _reproject_to_working(gdf, config.working_crs)

    gdf = _normalize_parcel_fields(gdf)

    # Measure parcels the bbox cuts before clipping them, so area_acres is the
    # whole parcel, not the part inside the study area.
    bbox_geom = box(*config.study_area.bbox)
    gdf["_whole_area_m2"] = _whole_area_m2_if_cut(gdf, bbox_geom)

    # Clip to study area
    gdf = gdf[gdf.intersects(bbox_geom)].copy()
    if gdf.empty:
        logger.warning("No parcels within study area")
        return None
    gdf = gpd.clip(gdf, bbox_geom)
    # Drop slivers / non-polygon leftovers from clipping
    gdf = gdf[gdf.geometry.geom_type.isin(("Polygon", "MultiPolygon"))].copy()

    from src.constants import MIN_PID_COVERAGE_FRACTION
    pid_coverage = (gdf["PID"].astype(bool)).mean() if len(gdf) else 0.0
    if len(gdf) and pid_coverage < MIN_PID_COVERAGE_FRACTION:
        logger.error(
            "Only %.0f%% of %d parcels have a PID (expected >=%.0f%%) — the "
            "source schema may have drifted (PID field renamed/moved). "
            "Aborting rather than writing an unusable PID list.",
            pid_coverage * 100, len(gdf), MIN_PID_COVERAGE_FRACTION * 100,
        )
        return None

    gdf = _dissolve_by_pid(gdf)
    whole_m2 = gdf.pop("_whole_area_m2")
    cut = whole_m2.notna()
    gdf["area_acres"] = whole_m2.where(cut, gdf.geometry.area) / SQ_M_PER_ACRE
    if cut.any():
        logger.info(
            "%d parcel(s) cross the study-area edge: their geometry is clipped, "
            "their area_acres is the whole parcel (%.0f acres in all, %.0f inside)",
            int(cut.sum()), float(gdf.loc[cut, "area_acres"].sum()),
            float(gdf.loc[cut].geometry.area.sum() / SQ_M_PER_ACRE),
        )

    processed.mkdir(parents=True, exist_ok=True)
    _write_gpkg(gdf, out_path)
    logger.info(
        "Parcels processed: %s (%d parcels, %d with PID)",
        out_path, len(gdf), int((gdf.get("PID", "") != "").sum()),
    )
    return out_path


def _mask_to_polygons(mask, transform, working_crs: str) -> gpd.GeoDataFrame:
    """Polygonize a 0/1 raster mask into dissolved polygons (pure, testable)."""
    from rasterio.features import shapes as rio_shapes
    from shapely.geometry import shape

    polys = [shape(g) for g, v in rio_shapes(mask, mask=mask == 1, transform=transform) if v == 1]
    if not polys:
        return gpd.GeoDataFrame(geometry=[], crs=working_crs)
    gdf = gpd.GeoDataFrame(geometry=polys, crs=working_crs)
    return gdf.dissolve().explode(index_parts=False).reset_index(drop=True)


def fetch_flood_polygons(
    bbox_working: tuple, service_url: str | None = None, size: int | None = None,
    timeout: int = 120, working_crs: str = WORKING_CRS,
) -> gpd.GeoDataFrame | None:
    """Export the coastal-flooding raster for the bbox and polygonize its extent.

    The flooding layers are raster (not vector), so we render them via the
    MapServer ``export`` op with a transparent background, treat any rendered
    (alpha>0) pixel as flooded, and polygonize. Best-effort: returns None on
    failure. Transform is built from the requested bbox/size so georeferencing
    doesn't depend on the server embedding it. ``bbox_working`` is assumed to be
    in ``working_crs``; its EPSG code is what's sent as bboxSR/imageSR — the
    service must support it (it accepts standard ArcGIS spatial references).
    """
    from rasterio.io import MemoryFile
    from rasterio.transform import from_bounds

    from src.constants import FLOOD_EXPORT_SIZE, FLOOD_SERVICE

    try:
        import numpy as np
        import requests
    except ImportError:
        logger.error("requests/numpy required for flood ingestion")
        return None

    base = (service_url or FLOOD_SERVICE).rstrip("/") + "/export"
    size = size or FLOOD_EXPORT_SIZE
    epsg = working_crs.split(":")[1]
    xmin, ymin, xmax, ymax = bbox_working
    params = {
        "bbox": f"{xmin},{ymin},{xmax},{ymax}", "bboxSR": epsg, "imageSR": epsg,
        "size": f"{size},{size}", "format": "png", "transparent": "true", "f": "image",
    }
    try:
        resp = requests.get(base, params=params, timeout=timeout)
        resp.raise_for_status()
        with MemoryFile(resp.content) as mem, mem.open() as src:
            arr = src.read()  # (bands, H, W); PNG RGBA -> 4 bands
        h, w = arr.shape[1], arr.shape[2]
        mask = (arr[3] > 0).astype("uint8") if arr.shape[0] >= 4 else (arr.sum(0) > 0).astype("uint8")
    except Exception:
        logger.exception("Flood raster export failed; skipping flood layer")
        return None

    transform = from_bounds(xmin, ymin, xmax, ymax, w, h)
    gdf = _mask_to_polygons(mask, transform, working_crs)
    # An empty result here means the export SUCCEEDED and genuinely found no
    # flooded pixels — distinct from the `None`-on-exception case above, which
    # means the fetch itself failed. Callers must be able to tell "checked, all
    # clear" from "couldn't check" (empty vs. None), so return the empty
    # GeoDataFrame rather than collapsing both cases to None. (Assigning a
    # scalar to a 0-row GeoDataFrame column is a safe no-op.)
    gdf["exclusion_reason"] = "flood_zone"
    return gdf


def wind_speed_at_hub(
    v_lo,
    v_hi,
    h_lo: float,
    h_hi: float,
    hub_m: float,
    alpha_min: float | None = None,
    alpha_max: float | None = None,
    min_ratio: float | None = None,
    nodata: float | None = None,
):
    """Estimate mean wind speed at ``hub_m`` from two GWA layers, per pixel.

    Fits a power-law shear profile v(z) = v_ref * (z / z_ref) ** alpha through
    the two layers, with alpha = ln(v_hi / v_lo) / ln(h_hi / h_lo) clamped to
    [alpha_min, alpha_max], and evaluates it at the hub height. The hub may lie
    between the layers or outside them (the default 30 m hub extrapolates down
    from the 50 m / 100 m layers).

    The profile is anchored on whichever layer is nearer the hub in log-height
    (50 m for a 30 m hub), so the anchor reproduces that layer exactly at its
    own height. Where alpha isn't clamped the anchor makes no difference (the
    profile passes through both points).

    Pixels are written as ``nodata`` (and never reach the math, so no NaN/inf
    is produced) when either layer is non-finite or <= 0, or when
    v_lo / v_hi < ``min_ratio`` — the canopy guard: a lower layer that has
    collapsed under the upper one (GWA's 10 m layer over forest is ~0.1 of its
    50 m layer) is not wind, and fitting a profile through it is meaningless.

    Returns ``(v_hub, stats)``: a float32 array shaped like the inputs, and a
    dict of pixel counts (``valid``, ``invalid``, ``rejected_ratio``,
    ``clamped_low``, ``clamped_high``) for logging and the caller's guards.
    """
    import numpy as np

    from src.constants import (
        WIND_MIN_LAYER_RATIO,
        WIND_NODATA,
        WIND_SHEAR_ALPHA_MAX,
        WIND_SHEAR_ALPHA_MIN,
    )

    alpha_min = WIND_SHEAR_ALPHA_MIN if alpha_min is None else alpha_min
    alpha_max = WIND_SHEAR_ALPHA_MAX if alpha_max is None else alpha_max
    min_ratio = WIND_MIN_LAYER_RATIO if min_ratio is None else min_ratio
    nodata = WIND_NODATA if nodata is None else nodata
    if not 0 < h_lo < h_hi:
        raise ValueError(f"need 0 < h_lo < h_hi, got {h_lo}, {h_hi}")
    if not hub_m > 0:
        raise ValueError(f"hub height must be > 0, got {hub_m}")
    if alpha_min > alpha_max:
        raise ValueError(f"alpha_min {alpha_min} > alpha_max {alpha_max}")

    v_lo = np.asarray(v_lo, dtype="float64")
    v_hi = np.asarray(v_hi, dtype="float64")
    if v_lo.shape != v_hi.shape:
        raise ValueError(f"layer shapes differ: {v_lo.shape} vs {v_hi.shape}")

    usable = np.isfinite(v_lo) & np.isfinite(v_hi) & (v_lo > 0) & (v_hi > 0)
    ratio = np.zeros(v_lo.shape)
    ratio[usable] = v_lo[usable] / v_hi[usable]
    rejected = usable & (ratio < min_ratio)
    valid = usable & ~rejected

    lo, hi = v_lo[valid], v_hi[valid]
    raw_alpha = np.log(hi / lo) / np.log(h_hi / h_lo)
    alpha = np.clip(raw_alpha, alpha_min, alpha_max)

    if abs(np.log(hub_m / h_lo)) <= abs(np.log(hub_m / h_hi)):
        anchor_v, anchor_h = lo, h_lo
    else:
        anchor_v, anchor_h = hi, h_hi

    out = np.full(v_lo.shape, nodata, dtype="float32")
    out[valid] = anchor_v * (hub_m / anchor_h) ** alpha
    stats = {
        "valid": int(valid.sum()),
        "invalid": int((~valid).sum()),
        "rejected_ratio": int(rejected.sum()),
        "clamped_low": int((raw_alpha < alpha_min).sum()),
        "clamped_high": int((raw_alpha > alpha_max).sum()),
    }
    return out, stats


# Source pixels of padding around the study-area window, so bilinear
# resampling has neighbours at the edges of the working grid.
_GWA_WINDOW_PAD_PX = 2


def _read_gwa_layer(url: str, bbox: tuple, working_crs: str, dst_transform, shape):
    """Windowed /vsicurl/ read of one GWA COG, resampled (bilinear) onto the
    working grid. Source nodata, resampling gaps, and any part of the study
    area outside the layer's coverage come back as NaN.

    The window is snapped to whole source pixels (offsets floored, far edges
    ceiled), padded, and clipped to the dataset before reading. rasterio floors
    a fractional window's offsets when reading and silently clips a window
    that runs off the dataset, but ``window_transform`` describes the window as
    given — so an unsnapped window georeferences the data up to a pixel (and
    for a clipped one, arbitrarily) away from where it is. Raises ValueError
    if the study area doesn't overlap the layer at all.
    """
    import math

    import numpy as np
    import rasterio
    from rasterio.warp import Resampling, reproject
    from rasterio.windows import Window
    from rasterio.windows import from_bounds as window_from_bounds

    with rasterio.open(f"/vsicurl/{url}") as src:
        b = gpd.GeoSeries([box(*bbox)], crs=working_crs).to_crs(src.crs).total_bounds
        win = window_from_bounds(b[0], b[1], b[2], b[3], src.transform)
        col0 = max(math.floor(win.col_off) - _GWA_WINDOW_PAD_PX, 0)
        row0 = max(math.floor(win.row_off) - _GWA_WINDOW_PAD_PX, 0)
        col1 = min(math.ceil(win.col_off + win.width) + _GWA_WINDOW_PAD_PX, src.width)
        row1 = min(math.ceil(win.row_off + win.height) + _GWA_WINDOW_PAD_PX, src.height)
        if col1 <= col0 or row1 <= row0:
            raise ValueError(f"study area {bbox} is outside the coverage of {url}")
        win = Window(col0, row0, col1 - col0, row1 - row0)
        data = src.read(1, window=win)
        src_transform = src.window_transform(win)
        src_crs, src_nodata = src.crs, src.nodata

    dst = np.full(shape, np.nan, dtype="float32")
    reproject(
        source=data, destination=dst,
        src_transform=src_transform, src_crs=src_crs,
        dst_transform=dst_transform, dst_crs=working_crs,
        src_nodata=src_nodata, dst_nodata=np.nan, resampling=Resampling.bilinear,
    )
    return dst


def wind_grid(config: Config):
    """The working grid wind.tif is written on: square ``cell_size_m`` pixels
    aligned with the candidate grid (which steps from the bbox's lower-left
    corner and covers a partial last row/column), so each 250 m cell maps to
    exactly one pixel. Returns ``(transform, (height, width))``."""
    import numpy as np
    from rasterio.transform import from_origin

    xmin, ymin, xmax, ymax = config.study_area.bbox
    res = float(config.cell_size_m)
    # Same counts as generate_candidate_grid's np.arange, float quirks included.
    w = max(len(np.arange(xmin, xmax, res)), 1)
    h = max(len(np.arange(ymin, ymax, res)), 1)
    return from_origin(xmin, ymin + h * res, res, res), (h, w)


def ingest_wind(config: Config) -> Path | None:
    """Ingest estimated mean wind speed at hub height into ``processed/wind.tif``.

    Reads only the study-area window of two Global Wind Atlas Canada COGs
    (``config.wind.gwa_layers``, default 50 m and 100 m) via /vsicurl/ range
    requests (no full download), resamples both onto the working grid, and
    writes the speed at ``config.wind.hub_height_m`` (default 30 m, a small
    off-grid turbine) from a per-pixel power-law shear fit — see
    `wind_speed_at_hub`. Makes the wind scorer use real wind resource instead
    of the terrain-exposure proxy (and clears the 'wind_proxy_only' penalty).

    Skips when wind.tif exists and the processed-data manifest says it was
    built from the current wind config. Otherwise (only reachable through
    `ingest --force` once a wind.tif exists, since the manifest check refuses
    stale layers without it) it rebuilds into a temp file and swaps it in
    atomically, then records the new inputs in the manifest. If the fetch or
    the guards fail, any existing wind.tif and its record are left exactly as
    they were, and None is returned with the reason logged.
    """
    import numpy as np

    from src.constants import (
        WIND_HTTP_MAX_RETRY,
        WIND_HTTP_TIMEOUT_S,
        WIND_INGEST_METHOD,
        WIND_MAX_REJECTED_FRACTION,
        WIND_MIN_LAYER_RATIO,
        WIND_NODATA,
    )
    from src.manifest import layer_is_current, record_layer

    processed = config.paths.processed
    working_crs = config.working_crs
    out_path = processed / "wind.tif"
    if out_path.exists():
        if layer_is_current(config, "wind"):
            logger.info("Wind already processed: %s", out_path)
            return out_path
        logger.warning("Rebuilding %s: it was built from different wind inputs "
                       "(the old file stays until the new one is complete)", out_path)
    keep_msg = "; the existing wind.tif is unchanged" if out_path.exists() else ""

    hub = config.wind.hub_height_m
    (h_lo, url_lo), (h_hi, url_hi) = sorted(config.wind.gwa_layers.items())
    bbox = config.study_area.bbox
    dst_transform, shape = wind_grid(config)

    gdal_env = {
        "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
        "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif",
        "GDAL_HTTP_TIMEOUT": str(WIND_HTTP_TIMEOUT_S),
        "GDAL_HTTP_MAX_RETRY": str(WIND_HTTP_MAX_RETRY),
        "GDAL_HTTP_RETRY_DELAY": "2",
    }
    layers = {}
    try:
        with rasterio.Env(**gdal_env):
            for height, url in ((h_lo, url_lo), (h_hi, url_hi)):
                layers[height] = _read_gwa_layer(url, bbox, working_crs, dst_transform, shape)
    except Exception as exc:
        failed = next(hh for hh in (h_lo, h_hi) if hh not in layers)
        logger.error("GWA %sm wind layer fetch failed (%s: %s); wind not ingested%s",
                     failed, type(exc).__name__, exc, keep_msg)
        logger.debug("GWA fetch traceback", exc_info=True)
        return None

    v_hub, stats = wind_speed_at_hub(layers[h_lo], layers[h_hi], h_lo, h_hi, hub)
    checked = stats["valid"] + stats["rejected_ratio"]
    if checked and stats["rejected_ratio"] / checked > WIND_MAX_REJECTED_FRACTION:
        logger.error(
            "GWA %sm layer is below %.0f%% of the %sm layer on %d of %d pixels (%.0f%%): the "
            "lower layer looks collapsed inside the canopy (as GWA's 10 m layer is over "
            "forest), so this layer pair can't give a usable shear profile. Wind not "
            "ingested%s. Use layers above the canopy, e.g. the default 50 m + 100 m.",
            h_lo, WIND_MIN_LAYER_RATIO * 100, h_hi, stats["rejected_ratio"], checked,
            100 * stats["rejected_ratio"] / checked, keep_msg,
        )
        return None
    if stats["valid"] == 0:
        logger.error("GWA wind layers had no valid pixels over the study area; wind not ingested%s",
                     keep_msg)
        return None
    if not np.isfinite(v_hub).all():  # guaranteed by wind_speed_at_hub; fail loudly if not
        raise RuntimeError("wind_speed_at_hub produced non-finite values")
    if stats["invalid"]:
        logger.warning("Wind: %d of %d cells have no usable GWA value (no coverage, nodata, "
                       "v <= 0, or failed the canopy guard); they score as 0 m/s",
                       stats["invalid"], v_hub.size)

    processed.mkdir(parents=True, exist_ok=True)
    h, w = shape
    meta = {"driver": "GTiff", "height": h, "width": w, "count": 1, "dtype": "float32",
            "crs": working_crs, "transform": dst_transform, "nodata": WIND_NODATA, "compress": "lzw"}
    # Write to a temp file and rename, so a failed write can't leave a partial
    # wind.tif (or clobber the previous one) that skip-if-exists would cache.
    with _atomic_output(out_path) as tmp_path:
        with rasterio.open(tmp_path, "w", **meta) as d:
            d.write(v_hub, 1)
            d.update_tags(
                hub_height_m=f"{hub:g}", method=WIND_INGEST_METHOD,
                gwa_layers=";".join(f"{lvl}m={url}" for lvl, url in sorted(config.wind.gwa_layers.items())),
            )
    # Record only after the swap: a crash in between leaves a new file with the
    # old record, which `score` refuses (safe), never the reverse.
    record_layer(config, "wind")

    valid = v_hub[v_hub != WIND_NODATA]
    extrapolating = "" if h_lo <= hub <= h_hi else " (extrapolated)"
    logger.info(
        "Wind processed: %s (%dx%d, %gm hub%s from GWA %sm/%sm, %.1f-%.1f m/s, median %.1f; "
        "%d px without a value (%d of them failed the canopy guard); shear exponent clamped low on %d px, "
        "high on %d px)",
        out_path, w, h, hub, extrapolating, h_lo, h_hi, float(valid.min()), float(valid.max()),
        float(np.median(valid)), stats["invalid"], stats["rejected_ratio"],
        stats["clamped_low"], stats["clamped_high"],
    )
    return out_path


def ingest_flood(config: Config) -> Path | None:
    """Ingest coastal-flooding polygons into ``processed/flood.gpkg`` (optional).

    Used automatically as a hard exclusion by the scorer, and clears the
    'no_flood_data' confidence penalty.
    """
    processed = config.paths.processed
    out_path = processed / "flood.gpkg"
    if out_path.exists():
        logger.info("Flood already processed: %s", out_path)
        return out_path

    gdf = fetch_flood_polygons(config.study_area.bbox, working_crs=config.working_crs)
    if gdf is None:
        # Fetch failed (network/service issue) — genuinely unknown, so don't
        # cache anything; the next run will retry rather than being stuck
        # believing "no flood zones" from a stale/wrong result.
        logger.info("Flood layer not ingested (service unreachable) — will retry next run")
        return None

    processed.mkdir(parents=True, exist_ok=True)
    _write_gpkg(gdf, out_path)
    if gdf.empty:
        # Export succeeded and genuinely found no flooded pixels in this bbox —
        # cache that (empty) result so confidence isn't penalized for "no flood
        # data" and so this doesn't re-hit the network on every future run.
        logger.info("Flood processed: %s (0 polygons — no flooding found in study area)", out_path)
    else:
        logger.info("Flood processed: %s (%d polygons, %.1f km²)",
                    out_path, len(gdf), gdf.geometry.area.sum() / 1e6)
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
    gdf = _reproject_to_working(gdf, config.working_crs)
    bbox_geom = box(*config.study_area.bbox)
    gdf = gdf[gdf.intersects(bbox_geom)].copy()
    if gdf.empty:
        return None
    gdf = gdf[gdf.geometry.geom_type.isin(("Polygon", "MultiPolygon"))].copy()

    processed.mkdir(parents=True, exist_ok=True)
    _write_gpkg(gdf, out_path)
    logger.info("Waterbodies processed: %s (%d features)", out_path, len(gdf))
    return out_path


def ingest_civic(config: Config) -> Path | None:
    """Ingest civic address points into ``processed/civic.gpkg`` (optional).

    Reads every GPKG, shapefile or GeoJSON under ``data/raw/civic/`` (the NS
    civic address file is often downloaded per county; the files are
    concatenated), reprojects to the working CRS and keeps the points inside
    the study area. scoring/access.py reads the output: a civic address in a
    cell scores access 100.
    """
    import pandas as pd

    raw = config.paths.raw
    processed = config.paths.processed
    out_path = processed / "civic.gpkg"

    if out_path.exists():
        logger.info("Civic addresses already processed: %s", out_path)
        return out_path

    civic_dir = raw / "civic"
    candidates = sorted(
        p for ext in ("*.gpkg", "*.shp", "*.geojson") for p in civic_dir.rglob(ext)
    ) if civic_dir.exists() else []
    if not candidates:
        logger.info("No civic address data found in %s (optional); skipping", civic_dir)
        return None

    frames = []
    for src_file in candidates:
        logger.info("Ingesting civic addresses from %s", src_file)
        frames.append(_reproject_to_working(gpd.read_file(src_file), config.working_crs))
    gdf = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs=config.working_crs)
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]

    bbox_geom = box(*config.study_area.bbox)
    gdf = gdf[gdf.intersects(bbox_geom)].copy()
    if gdf.empty:
        logger.info("No civic addresses within study area")
        return None

    processed.mkdir(parents=True, exist_ok=True)
    _write_gpkg(gdf, out_path)
    logger.info("Civic addresses processed: %s (%d points)", out_path, len(gdf))
    return out_path


def run_ingest(config: Config, logger: logging.Logger, force: bool = False) -> dict[str, Path | None]:
    """Run all ingestion steps. Returns dict of layer_name -> processed path.

    ``force`` clears data/processed/ outputs and rebaselines the manifest if
    the config (study area/cell size/CRS) no longer matches what's cached
    there; without it, a mismatch raises rather than silently mixing old and
    new outputs. See src/manifest.py.
    """
    from src.manifest import check_and_update_manifest

    processed = config.paths.processed
    processed.mkdir(parents=True, exist_ok=True)
    check_and_update_manifest(config, force=force)

    results = {}
    results["dem"] = ingest_dem(config)
    results["streams"] = ingest_streams(config)
    results["roads"] = ingest_roads_from_osm(config)
    results["buildings"] = ingest_buildings(config)
    results["land_cover"] = ingest_land_cover(config)
    results["crown_land"] = ingest_crown_land(config)
    results["protected_areas"] = ingest_protected_areas(config)
    results["flood"] = ingest_flood(config)
    results["wind"] = ingest_wind(config)
    results["waterbodies"] = ingest_waterbodies(config)
    results["civic"] = ingest_civic(config)
    results["parcels"] = ingest_parcels(config)

    logger.info("=== Ingestion summary ===")
    for name, path in results.items():
        status = f"OK ({path})" if path else "MISSING"
        logger.info("  %s: %s", name, status)

    return results

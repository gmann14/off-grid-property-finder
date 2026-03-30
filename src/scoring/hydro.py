"""Micro-hydro scoring — stream search, intake/outfall pairs, power estimation."""

import logging
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import LineString
from shapely.strtree import STRtree

from src.config import Config
from src.constants import (
    GRAVITY,
    DEFAULT_EFFICIENCY,
    HYDRO_PAIR_SEPARATIONS,
    HYDRO_POWER_THRESHOLDS,
    MIN_DRAINAGE_AREA_KM2,
    MIN_HEAD_M,
    SPECIFIC_RUNOFF_LOW,
    STREAM_BUFFER_M,
)
from src.scoring.registry import register

logger = logging.getLogger("property_finder")


def _estimate_flow_rate(drainage_area_km2: float) -> float:
    """Estimate low-flow rate (m³/s) from drainage area using specific runoff."""
    return drainage_area_km2 * SPECIFIC_RUNOFF_LOW / 1000.0


def _estimate_power(flow_m3s: float, head_m: float, efficiency: float = DEFAULT_EFFICIENCY) -> float:
    """Estimate hydro power in watts: P = η * ρ * g * Q * H."""
    rho = 1000.0  # water density kg/m³
    return efficiency * rho * GRAVITY * flow_m3s * head_m


def _lookup_score(value: float, thresholds: list[tuple]) -> int:
    for low, high, score in thresholds:
        if low <= value < high:
            return score
    return 0


def _sample_dem(dem_src, x: float, y: float) -> float | None:
    """Sample DEM elevation at a point. Returns None if nodata or error."""
    try:
        vals = list(dem_src.sample([(x, y)]))
        elev = float(vals[0][0])
        nodata = dem_src.nodata
        if nodata is not None and elev == nodata:
            return None
        return elev
    except Exception:
        return None


def _compute_head_along_river(
    dem_src, stream_geom, cell_geom, search_dist: float = 500.0
) -> tuple[float, bool]:
    """Compute available head by sampling DEM along the river near the cell.

    Returns (head_m, had_data): head_m is the elevation difference along the
    river, and had_data indicates whether DEM data was available along the
    stream (False = coverage gap, True = we got readings even if head is low).
    """
    cell_centroid = cell_geom.centroid
    nearest_dist_along = stream_geom.project(cell_centroid)
    stream_length = stream_geom.length

    if stream_length < 10:
        return 0.0, False

    start = max(0.0, nearest_dist_along - search_dist)
    end = min(stream_length, nearest_dist_along + search_dist)
    sample_length = end - start

    if sample_length < 20:
        return 0.0, False

    n_samples = max(5, min(20, int(sample_length / 25)))
    elevations = []
    for i in range(n_samples + 1):
        dist = start + (end - start) * i / n_samples
        pt = stream_geom.interpolate(dist)
        elev = _sample_dem(dem_src, pt.x, pt.y)
        if elev is not None and elev > -100:
            elevations.append(elev)

    if len(elevations) < 2:
        return 0.0, False

    head = max(elevations) - min(elevations)
    min_elev = min(elevations)

    # If the stream is near sea level (< 5m), it's likely tidal/estuarine.
    # Wide, slow tidal rivers have no usable head for micro-hydro.
    if min_elev < 5.0:
        head = min(head, min_elev)  # Cap head at stream elevation

    return head, True


def _estimate_drainage_area(stream, all_streams: gpd.GeoDataFrame) -> float:
    """Estimate drainage area for a stream segment.

    Uses attribute data if available, otherwise estimates from segment
    length and stream class.  NSHN LINE_CLASS doesn't reliably indicate
    stream order — a named brook like Martins Brook can be class 1 — so
    segment length is a better proxy for catchment size.
    """
    # Try direct attribute
    for col in ("drainage_area_km2", "drain_area", "wsarea"):
        if col in stream.index and pd.notna(stream.get(col)):
            return float(stream[col])

    seg_len_m = stream.geometry.length

    # Use segment length as the primary proxy.  Empirical relationship for
    # Nova Scotia coastal streams: drainage ≈ (length_km)^1.5 × 2
    # A 1 km segment ≈ 2 km², 500m ≈ 0.7 km², 2 km ≈ 5.7 km².
    seg_len_km = seg_len_m / 1000.0
    base = max(MIN_DRAINAGE_AREA_KM2, 2.0 * seg_len_km ** 1.5)

    # Boost for higher LINE_CLASS (when it is meaningful)
    line_class = stream.get("LINE_CLASS", 1)
    if pd.isna(line_class):
        line_class = 1
    line_class = int(line_class)
    if line_class >= 3:
        base = max(base, 25.0)
    elif line_class >= 2:
        base = max(base, 5.0)

    # Connectors (WACO) carry accumulated upstream drainage
    feat_code = stream.get("FEAT_CODE", "")
    if isinstance(feat_code, str) and feat_code.startswith("WACO"):
        base = max(base, 10.0)

    return base


def _filter_to_flowing_water(streams: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Filter NSHN stream features to flowing water only.

    Excludes lake shorelines, tidal outflows, wharves, dams, boundaries,
    and island shoreline features.
    """
    if "FEAT_CODE" not in streams.columns:
        return streams

    exclude_prefixes = ("WALK", "WATO", "WAWH", "WADM", "WABD")
    excluded = streams["FEAT_CODE"].str.startswith(exclude_prefixes)
    excluded |= streams["FEAT_CODE"].str.contains("IS", na=False)
    n_before = len(streams)
    result = streams[~excluded].reset_index(drop=True)
    logger.info("Filtered to %d flowing water features (from %d total)",
                 len(result), n_before)
    return result


@register("hydro")
def score_hydro(candidates: gpd.GeoDataFrame, config: Config) -> pd.Series:
    """Score cells by micro-hydro potential.

    Approach:
    1. Find streams within STREAM_BUFFER_M of each cell (property can
       access a nearby river even if it doesn't directly cross the cell).
    2. Estimate drainage area from stream class/attributes.
    3. Sample DEM along a longer river stretch (up to 500m each direction)
       to get realistic head available for a penstock run.
    4. Score based on estimated power output.
    """
    streams_path = config.paths.processed / "streams.gpkg"
    dem_path = config.paths.processed / "dem.tif"

    if not streams_path.exists():
        logger.warning("Streams data not found; hydro scores = 0")
        return pd.Series(0.0, index=candidates.index)

    streams = gpd.read_file(streams_path)
    if streams.crs and str(streams.crs) != config.working_crs:
        streams = streams.to_crs(config.working_crs)

    streams = _filter_to_flowing_water(streams)

    stream_tree = STRtree(streams.geometry.values)

    has_dem = dem_path.exists()
    dem_src = None
    cell_zonal = None
    if has_dem:
        try:
            import rasterio
            from rasterstats import zonal_stats

            dem_src = rasterio.open(dem_path)

            # Precompute elevation range per cell — used as fallback when
            # along-river DEM sampling has no coverage.
            zs = zonal_stats(
                candidates.geometry,
                str(dem_path),
                stats=["range"],
            )
            cell_zonal = {
                idx: s.get("range")
                for idx, s in zip(candidates.index, zs)
            }
            n_with_range = sum(1 for v in cell_zonal.values() if v is not None and v > 0)
            logger.info("Hydro: precomputed cell elevation ranges for %d cells", n_with_range)
        except Exception:
            has_dem = False

    scores = []
    for idx, cell in candidates.iterrows():
        # Search for streams within buffer distance of cell
        # A property near a river can still use it for hydro
        buffered = cell.geometry.buffer(STREAM_BUFFER_M)
        hit_idxs = stream_tree.query(buffered, predicate="intersects")

        if len(hit_idxs) == 0:
            scores.append(0)
            continue

        best_power = 0.0

        for i in hit_idxs:
            stream = streams.iloc[i]
            stream_geom = stream.geometry

            drainage_area = _estimate_drainage_area(stream, streams)
            if drainage_area < MIN_DRAINAGE_AREA_KM2:
                continue

            flow_rate = _estimate_flow_rate(drainage_area)

            feat_code = stream.get("FEAT_CODE", "")
            is_connector = isinstance(feat_code, str) and feat_code.startswith("WACO")

            # Get head from DEM along a longer river stretch
            head = 0.0
            had_dem_data = False
            if has_dem and dem_src is not None:
                head, had_dem_data = _compute_head_along_river(
                    dem_src, stream_geom, cell.geometry
                )

                # Fallback: if along-river sampling had NO DEM coverage at all
                # (not "had data but stream is flat"), use the cell's own
                # elevation range as proxy.  Only for true coverage gaps
                # and only for real river segments (not short connectors).
                if (not had_dem_data and cell_zonal is not None
                        and stream_geom.length >= 100 and not is_connector):
                    cell_range = cell_zonal.get(idx)
                    if cell_range is not None and cell_range >= MIN_HEAD_M:
                        head = cell_range

            if head < MIN_HEAD_M and not had_dem_data and not is_connector:
                # Only use fallbacks when DEM had no data along the stream
                # and the feature is a real river/brook, not a WACO connector.
                minz = stream.get("MINZ")
                maxz = stream.get("MAXZ")
                if pd.notna(minz) and pd.notna(maxz) and abs(maxz - minz) > 0.1:
                    head = abs(maxz - minz)
                else:
                    head = stream_geom.length * 0.02  # 2% slope proxy

            if head < MIN_HEAD_M:
                continue

            power = _estimate_power(flow_rate, head)
            best_power = max(best_power, power)

        scores.append(_lookup_score(best_power, HYDRO_POWER_THRESHOLDS))

    if dem_src is not None:
        dem_src.close()

    return pd.Series(scores, index=candidates.index, dtype=float)

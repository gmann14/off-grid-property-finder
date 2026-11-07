"""Micro-hydro scoring — stream search, intake/outfall pairs, power estimation."""

import logging
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from shapely.geometry import LineString
from shapely.strtree import STRtree

from src.config import Config
from src.constants import (
    GRAVITY,
    DEFAULT_EFFICIENCY,
    FLOW_ACCUM_MIN_VALID_CELLS,
    HYDRO_DRAINAGE_JUNCTION_SKIP_M,
    HYDRO_DRAINAGE_WINDOW_M,
    HYDRO_PAIR_SEPARATIONS,
    HYDRO_POWER_THRESHOLDS,
    HYDRO_PROFILE_STEP_M,
    MIN_DRAINAGE_AREA_KM2,
    MIN_HEAD_M,
    SPECIFIC_RUNOFF_LOW,
    STREAM_BUFFER_M,
)
from src.scoring.registry import register

logger = logging.getLogger("property_finder")

# NSHN line features that are not flowing water: lake shorelines (WALK),
# tidal outflows (WATO), wharves (WAWH), dams (WADM), boundaries (WABD) and
# the coastline (WACO, incl. WACORV tidal river reaches).
_NON_FLOWING_PREFIXES = ("WALK", "WATO", "WAWH", "WADM", "WABD", "WACO")
_COASTLINE_LINE_CLASS = 3


def flow_accumulation_valid(path: Path) -> bool:
    """True if a D8 flow-accumulation raster actually propagated to river scale.

    Guards against the common failure where D8 routing dies on large flats and
    accumulation maxes out at a few dozen cells (useless for drainage area).
    """
    if not path.exists():
        return False
    try:
        import rasterio
        with rasterio.open(path) as src:
            arr = src.read(1)
            nd = src.nodata
            valid = arr[arr != nd] if nd is not None else arr
            if valid.size == 0:
                return False
            return float(valid.max()) >= FLOW_ACCUM_MIN_VALID_CELLS
    except Exception as exc:
        logger.warning("Hydro: could not read flow accumulation %s (%s: %s); treating it "
                       "as invalid", path, type(exc).__name__, exc)
        return False


class _DrainageGrid:
    """Flow accumulation as drainage area (km²), 3×3-max filtered.

    The 3×3 max tolerates the mapped stream line sitting one routing pixel off
    the DEM-derived channel. Nodata (and pixels whose whole 3×3 is nodata) are
    NaN.
    """

    def __init__(self, facc_src):
        from scipy.ndimage import maximum_filter

        arr = facc_src.read(1).astype("float64")
        nd = facc_src.nodata
        bad = ~np.isfinite(arr)
        if nd is not None and not np.isnan(nd):
            bad |= arr == nd
        arr[bad] = -np.inf
        arr = maximum_filter(arr, size=3, mode="constant", cval=-np.inf)
        arr[np.isneginf(arr)] = np.nan
        self.km2 = arr * (abs(facc_src.res[0]) * abs(facc_src.res[1])) / 1e6
        self.transform = facc_src.transform

    def values(self, xy: np.ndarray) -> np.ndarray:
        return _grid_lookup(self.km2, self.transform, xy)


def _grid_lookup(arr: np.ndarray, transform, xy: np.ndarray) -> np.ndarray:
    """Value of the pixel containing each (x, y); NaN outside the grid."""
    out = np.full(len(xy), np.nan)
    if len(xy) == 0:
        return out
    cols, rows = ~transform * (xy[:, 0], xy[:, 1])
    r = np.floor(rows).astype(np.int64)
    c = np.floor(cols).astype(np.int64)
    inside = (r >= 0) & (r < arr.shape[0]) & (c >= 0) & (c < arr.shape[1])
    out[inside] = arr[r[inside], c[inside]]
    return out


def _along_line_max(values: np.ndarray, half_window: int) -> np.ndarray:
    """Max of the valid values within ±half_window samples (NaN if none)."""
    from scipy.ndimage import maximum_filter1d

    v = np.where(np.isnan(values), -np.inf, values)
    m = maximum_filter1d(v, size=2 * half_window + 1, mode="constant", cval=-np.inf)
    return np.where(np.isneginf(m), np.nan, m)


def _line_samples(geom, step: float = HYDRO_PROFILE_STEP_M) -> tuple[np.ndarray, np.ndarray]:
    """Distances s = 0, step, 2·step, ... (≤ length) along a line and their (x, y)."""
    s = np.arange(0.0, geom.length + 1e-9, step)
    xy = shapely.get_coordinates(shapely.line_interpolate_point(geom, s))
    return s, xy


def _drainage_profile(grid: _DrainageGrid, s: np.ndarray, xy: np.ndarray,
                      length: float, flowdir) -> np.ndarray:
    """Drainage area (km²) at each sample, read along the stream's own line.

    Each sample takes the max of the 3×3 reads within HYDRO_DRAINAGE_WINDOW_M
    along the same line, so a mapped line that wanders off the routed channel
    still finds its own channel where it crosses back over it. Reading along
    the line (rather than snapping to the biggest accumulation within a
    radius) keeps a brook from picking up a bigger river running nearby. The
    last HYDRO_DRAINAGE_JUNCTION_SKIP_M of a line drawn in its flow direction
    (FLOWDIR = 1) is left out of the reads, because there it joins its
    receiving water and the 3×3 read is the receiving water's drainage; a
    line shorter than that keeps only its upstream end.
    """
    raw = grid.values(xy)
    if _flowdir_is_downstream(flowdir) and len(raw) > 0:
        junction = s >= length - HYDRO_DRAINAGE_JUNCTION_SKIP_M
        junction[0] = False
        raw = np.where(junction, np.nan, raw)
    half = max(0, int(round(HYDRO_DRAINAGE_WINDOW_M / HYDRO_PROFILE_STEP_M)))
    return _along_line_max(raw, half)


def _flowdir_is_downstream(flowdir) -> bool:
    """NSHN FLOWDIR = 1: the line is digitised in its flow direction."""
    try:
        return int(flowdir) == 1
    except (TypeError, ValueError):
        return False


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

    # Tidal/estuarine reaches near sea level have no usable head ONLY when they
    # are also essentially flat. A steep stream dropping into tidewater keeps its
    # head — so test gradient, don't clamp head to the (near-zero) min elevation.
    if min_elev < 5.0:
        gradient = head / sample_length if sample_length > 0 else 0.0
        if gradient < 0.005:  # < 0.5% slope near sea level → flat tidal channel
            head = 0.0

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

    # Double-line rivers (LINE_CLASS 2 banks, and the features drawn inside
    # them) are bigger than their segment length suggests. There is no larger
    # boost for LINE_CLASS 3 or WACO features: those are the sea-level
    # coastline, which _filter_to_flowing_water drops.
    line_class = stream.get("LINE_CLASS", 1)
    if pd.isna(line_class):
        line_class = 1
    line_class = int(line_class)
    if line_class >= 2 and line_class != _COASTLINE_LINE_CLASS:
        base = max(base, 5.0)

    return base


def _filter_to_flowing_water(streams: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Filter NSHN stream features to flowing water only.

    Excludes lake shorelines, tidal outflows, wharves, dams, boundaries,
    island shoreline features, and the coastline: every WACO* code (WACO20/25
    coastline, WACORV* tidal river banks and centrelines, WACO*F0 closing
    lines) carries MINZ = MAXZ = 0.2 m and sits on DEM pixels at or near sea
    level, and LINE_CLASS 3 is the coastline class. Tidal water has no head.
    """
    if "FEAT_CODE" not in streams.columns and "LINE_CLASS" not in streams.columns:
        return streams

    excluded = pd.Series(False, index=streams.index)
    if "FEAT_CODE" in streams.columns:
        codes = streams["FEAT_CODE"].fillna("").astype(str)
        excluded |= codes.str.startswith(_NON_FLOWING_PREFIXES)
        excluded |= codes.str.contains("IS", na=False)
    if "LINE_CLASS" in streams.columns:
        excluded |= pd.to_numeric(streams["LINE_CLASS"], errors="coerce").eq(_COASTLINE_LINE_CLASS)
    n_before = len(streams)
    result = streams[~excluded].reset_index(drop=True)
    logger.info("Filtered to %d flowing water features (from %d total)",
                 len(result), n_before)
    return result


def _open_dem(dem_path: Path):
    """Open the DEM for head sampling, or None (logged) if it can't be used.

    Without it, head falls back to the stream's MINZ/MAXZ attributes or a 2%
    slope guess, so say so loudly rather than scoring on that silently.
    """
    if not dem_path.exists():
        logger.warning("Hydro: no DEM at %s; head falls back to stream MINZ/MAXZ "
                       "or a 2%% slope guess", dem_path)
        return None
    try:
        import rasterio
        return rasterio.open(dem_path)
    except Exception as exc:  # rasterio raises RasterioIOError, but be explicit
        logger.warning("Hydro: could not open DEM %s (%s: %s); head falls back to "
                       "stream MINZ/MAXZ or a 2%% slope guess",
                       dem_path, type(exc).__name__, exc)
        return None


def _cell_elevation_ranges(candidates: gpd.GeoDataFrame, dem_path: Path) -> dict | None:
    """Per-cell DEM elevation range (max - min), keyed by candidate index.

    Used only as a fallback head for streams with no DEM coverage along them.
    Returns None (logged) if the precompute fails; the DEM is still used for
    along-stream head either way.
    """
    try:
        from src.zonal import grid_zonal_stats
        ranges = grid_zonal_stats(candidates.geometry, dem_path, stats=("range",))["range"]
    except Exception as exc:
        logger.warning("Hydro: per-cell elevation-range precompute failed (%s: %s); "
                       "streams with no DEM coverage get no cell-range fallback head",
                       type(exc).__name__, exc)
        return None
    cell_zonal = {
        idx: (None if np.isnan(r) else float(r))
        for idx, r in zip(candidates.index, ranges)
    }
    n_with_range = sum(1 for v in cell_zonal.values() if v is not None and v > 0)
    logger.info("Hydro: precomputed cell elevation ranges for %d cells", n_with_range)
    return cell_zonal


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

    # Prefer a validated flow-accumulation raster for drainage area; otherwise
    # fall back to the segment-length proxy (and the confidence score reflects it).
    facc_path = config.paths.processed / "flow_accumulation.tif"
    drainage_grid = None
    if flow_accumulation_valid(facc_path):
        try:
            import rasterio
            with rasterio.open(facc_path) as facc_src:
                drainage_grid = _DrainageGrid(facc_src)
            logger.info("Hydro: using validated flow-accumulation raster for drainage area")
        except Exception as exc:
            logger.warning("Hydro: could not read flow accumulation %s (%s: %s)",
                           facc_path, type(exc).__name__, exc)
            drainage_grid = None
    if drainage_grid is None:
        logger.info("Hydro: flow-accumulation unavailable/invalid; using segment-length proxy")
    drainage_profiles: dict[int, tuple[np.ndarray, np.ndarray]] = {}

    dem_src = _open_dem(dem_path)
    has_dem = dem_src is not None
    # Per-cell elevation range: only a fallback head for streams with no DEM
    # coverage along them. Its failure must not switch the DEM off for head.
    cell_zonal = _cell_elevation_ranges(candidates, dem_path) if has_dem else None

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

            # Drainage area at the point on the stream nearest this cell,
            # read along the stream's own line (see _drainage_profile).
            drainage_area = None
            if drainage_grid is not None:
                if i not in drainage_profiles:
                    s, xy = _line_samples(stream_geom)
                    drainage_profiles[i] = (s, _drainage_profile(
                        drainage_grid, s, xy, stream_geom.length, stream.get("FLOWDIR")))
                s, prof = drainage_profiles[i]
                j = int(np.clip(np.rint(stream_geom.project(cell.geometry.centroid)
                                        / HYDRO_PROFILE_STEP_M), 0, len(s) - 1))
                if len(s) and np.isfinite(prof[j]):
                    drainage_area = float(prof[j])
            if drainage_area is None:
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

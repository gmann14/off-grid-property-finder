"""Micro-hydro scoring — stream search, intake/outfall pairs, power estimation."""

import logging
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from shapely.strtree import STRtree

from src.config import Config
from src.constants import (
    GRAVITY,
    DEFAULT_EFFICIENCY,
    FLOW_ACCUM_MIN_VALID_CELLS,
    HYDRO_CHANNEL_SNAP_PX,
    HYDRO_DRAINAGE_JUNCTION_SKIP_M,
    HYDRO_DRAINAGE_WINDOW_M,
    HYDRO_HEAD_CONDITION_M,
    HYDRO_HEAD_SMOOTH_M,
    HYDRO_PAIR_SEPARATIONS,
    HYDRO_POWER_THRESHOLDS,
    HYDRO_PROFILE_STEP_M,
    MIN_DRAINAGE_AREA_KM2,
    MIN_HEAD_M,
    SEA_LEVEL_M,
    SPECIFIC_RUNOFF_LOW,
    STREAM_BUFFER_M,
)
from src.scoring.bands import band_score
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
    Only land counts: a raster routed with the sea in it piles the whole
    coast's flow into sea cells, which pass the check even when the land's
    routing failed. Sea cells are nodata in rasters routed by the current
    src.dem; for older ones they are masked from the routing DEM beside it.
    """
    if not path.exists():
        return False
    try:
        import rasterio
        with rasterio.open(path) as src:
            arr = src.read(1)
            nd = src.nodata
            land = arr != nd if nd is not None else np.isfinite(arr)
            sea = _sea_on_grid(path.parent, src)
            if sea is not None:
                land &= ~sea
            valid = arr[land]
            if valid.size == 0:
                return False
            return float(valid.max()) >= FLOW_ACCUM_MIN_VALID_CELLS
    except Exception as exc:
        logger.warning("Hydro: could not read flow accumulation %s (%s: %s); treating it "
                       "as invalid", path, type(exc).__name__, exc)
        return False


def _sea_on_grid(processed: Path, facc_src) -> np.ndarray | None:
    """Sea (DEM at or below SEA_LEVEL_M, or no DEM) on the accumulation's grid,
    from the routing DEM (dem_flowres.tif, else dem.tif) when one shares it.
    None if neither does."""
    import rasterio

    for name in ("dem_flowres.tif", "dem.tif"):
        p = processed / name
        if not p.exists():
            continue
        with rasterio.open(p) as d:
            if d.shape != facc_src.shape or not d.transform.almost_equals(facc_src.transform):
                continue
            z = d.read(1)
            sea = ~np.isfinite(z) | (z <= SEA_LEVEL_M)
            if d.nodata is not None:
                sea |= z == d.nodata
            return sea
    logger.debug("Hydro: no DEM on the flow-accumulation grid; validity check uses every cell")
    return None


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


def _channel_elevations(dem_src, xy: np.ndarray) -> np.ndarray:
    """Channel-bed elevation at each (x, y): the lowest DEM pixel within
    HYDRO_CHANNEL_SNAP_PX pixels, so a mapped line a few metres up the bank
    still reads the channel. NaN where the DEM has no data or no coverage."""
    r = HYDRO_CHANNEL_SNAP_PX
    out = np.full(len(xy), np.nan)
    if len(xy) == 0:
        return out
    dem = dem_src.read(1).astype("float32")
    nodata = dem_src.nodata
    transform = dem_src.transform
    if nodata is not None and not np.isnan(nodata):
        dem[dem == nodata] = np.nan
    dem[~np.isfinite(dem)] = np.nan
    cols, rows = ~transform * (xy[:, 0], xy[:, 1])
    rows = np.floor(rows).astype(np.int64)
    cols = np.floor(cols).astype(np.int64)
    inside = (rows >= 0) & (rows < dem.shape[0]) & (cols >= 0) & (cols < dem.shape[1])
    ri, ci = rows[inside], cols[inside]
    best = np.full(len(ri), np.nan, dtype="float32")
    for dr in range(-r, r + 1):
        for dc in range(-r, r + 1):
            rr = np.clip(ri + dr, 0, dem.shape[0] - 1)
            cc = np.clip(ci + dc, 0, dem.shape[1] - 1)
            best = np.fmin(best, dem[rr, cc])
    # the pixel under the point itself must have data
    best[np.isnan(dem[ri, ci])] = np.nan
    out[inside] = best
    return out


def _running_median(z: np.ndarray, half: int) -> np.ndarray:
    """NaN-aware running median over ±half samples (edges padded)."""
    import warnings

    from numpy.lib.stride_tricks import sliding_window_view

    if half <= 0 or len(z) == 0:
        return z.copy()
    padded = np.pad(z, half, mode="edge")
    with warnings.catch_warnings():  # all-NaN window -> NaN (no DEM there)
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.nanmedian(sliding_window_view(padded, 2 * half + 1), axis=1)


def _flows_reversed(z: np.ndarray, flowdir) -> bool:
    """True if the line runs upstream in its digitised direction.

    NSHN FLOWDIR = 1 lines are digitised downstream. Otherwise (banks of
    double-line rivers, connectors) the flow direction is the DEM's downhill
    direction along the line: the sign of a least-squares slope of elevation
    against distance.
    """
    if _flowdir_is_downstream(flowdir):
        return False
    ok = np.isfinite(z)
    if ok.sum() < 2:
        return False
    idx = np.arange(len(z))[ok]
    return bool(np.polyfit(idx, z[ok], 1)[0] > 0)


def _condition_window() -> int:
    """HYDRO_HEAD_CONDITION_M in samples, rounded to an even count."""
    return 2 * int(round(HYDRO_HEAD_CONDITION_M / HYDRO_PROFILE_STEP_M / 2))


def _conditioned_profiles(z: np.ndarray, w: int) -> tuple[np.ndarray, np.ndarray]:
    """Carved and filled versions of a channel profile ordered upstream first.

    carved[j] is the lowest sample in z[j - w .. j] (the channel can't be
    higher than anywhere just upstream of it), filled[j] the highest in
    z[j .. j + w] (nor lower than anywhere just downstream). w is an even
    number of samples; NaNs (no DEM) are skipped over.
    """
    from scipy.ndimage import maximum_filter1d, minimum_filter1d

    if w % 2:
        raise ValueError(f"conditioning window must be an even sample count, got {w}")
    nan = np.isnan(z)
    carved = minimum_filter1d(np.where(nan, np.inf, z), w + 1, mode="nearest", origin=w // 2)
    filled = maximum_filter1d(np.where(nan, -np.inf, z), w + 1, mode="nearest", origin=-(w // 2))
    return (np.where(np.isfinite(carved), carved, np.nan),
            np.where(np.isfinite(filled), filled, np.nan))


def _pair_powers(z: np.ndarray, z_valid: np.ndarray, drainage: np.ndarray,
                 n_own: int, k: int) -> np.ndarray:
    """Continuous low-flow power (W) of each intake/outfall pair (a, a + k).

    z is the channel profile, upstream first. a runs over the line's own
    samples (0 .. n_own-1); a + k may run on into the downstream continuation.
    Gross head is the upstream minus the downstream elevation along the flow
    direction, read off the profile conditioned so water can't run uphill
    within HYDRO_HEAD_CONDITION_M: the smaller of the drops on the carved and
    the filled profile. A DEM bump along the channel is then no head, whether
    the pair starts on its crest (the carved drop is 0) or ends in its trough
    (the filled drop is 0). On NSHN-flat reaches (MAXZ - MINZ < 1 m) this
    cuts the 50-500 m pairs that clear MIN_HEAD_M on DEM undulation alone
    from 0.40% to 0.04%; on steep reaches the median 500 m head is unchanged.
    The window is finite so a rise further down the flow path (a road fill,
    the next line's DEM) doesn't raise the filled profile of every sample
    above it. Flow is taken at the pair's upper end (the intake). Pairs under
    MIN_HEAD_M of head, under MIN_DRAINAGE_AREA_KM2 at the intake, or with
    either end off the DEM get 0.
    """
    n_upper = min(n_own, len(z) - k)
    if n_upper <= 0:
        return np.zeros(0)
    carved, filled = _conditioned_profiles(z, _condition_window())
    a = np.arange(n_upper)
    b = a + k
    head = np.fmin(carved[a] - carved[b], filled[a] - filled[b])
    area = drainage[a]
    ok = z_valid[a] & z_valid[b] & (head >= MIN_HEAD_M) & (area >= MIN_DRAINAGE_AREA_KM2)
    power = np.zeros(n_upper)
    power[ok] = _estimate_power(_estimate_flow_rate(area[ok]), head[ok])
    return power


def _explode_lines(streams: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """One single-part LineString per row (multi-part NSHN features are merged
    where their parts join end to start, then split)."""
    geoms = shapely.line_merge(streams.geometry.values, directed=True)
    out = streams.set_geometry(gpd.GeoSeries(geoms, index=streams.index, crs=streams.crs))
    out = out.explode(index_parts=False).reset_index(drop=True)
    return out[out.geometry.geom_type == "LineString"].reset_index(drop=True)


def _downstream_successors(streams: gpd.GeoDataFrame, drainage_at_start: np.ndarray) -> np.ndarray:
    """For each FLOWDIR = 1 line, the FLOWDIR = 1 line that starts where it
    ends (the biggest one at a split), or -1. Lines digitised in their flow
    direction chain into the downstream flow path the SPEC's pairs run along.
    """
    succ = np.full(len(streams), -1, dtype=np.int64)
    down = np.array([_flowdir_is_downstream(f) for f in streams.get("FLOWDIR", [None] * len(streams))])
    if not down.any():
        return succ
    coords = [shapely.get_coordinates(g) for g in streams.geometry.values]

    def key(c):
        return (round(float(c[0]), 1), round(float(c[1]), 1))

    starts: dict[tuple, list[int]] = {}
    for i in np.flatnonzero(down):
        starts.setdefault(key(coords[i][0]), []).append(int(i))
    for i in np.flatnonzero(down):
        nxt = [j for j in starts.get(key(coords[i][-1]), []) if j != i]
        if nxt:
            succ[i] = max(nxt, key=lambda j: np.nan_to_num(drainage_at_start[j], nan=-1.0))
    return succ


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
    """Score cells by micro-hydro potential (SPEC §3.1).

    1. Sample every flowing NSHN line every HYDRO_PROFILE_STEP_M: channel
       elevation from the DEM, drainage area from the flow-accumulation
       raster (else the segment-length proxy).
    2. Orient each line downstream (FLOWDIR = 1, else the DEM's downhill
       direction) and chain FLOWDIR = 1 lines into the line they flow into,
       so pairs follow the downstream flow path past segment breaks.
    3. Intake/outfall pairs HYDRO_PAIR_SEPARATIONS apart: gross head =
       upstream minus downstream elevation, flow at the intake (upper end).
    4. A cell's hydro score is its best pair with at least one end within
       STREAM_BUFFER_M of the cell, banded by continuous low-flow power.

    Lines with no DEM coverage at all fall back to per-cell head from the
    cell's elevation range, the line's MINZ/MAXZ, or a 2% slope guess.
    """
    streams_path = config.paths.processed / "streams.gpkg"
    dem_path = config.paths.processed / "dem.tif"

    if not streams_path.exists():
        logger.warning("Streams data not found; hydro scores = 0")
        return pd.Series(0.0, index=candidates.index)

    streams = gpd.read_file(streams_path)
    if streams.crs and str(streams.crs) != config.working_crs:
        streams = streams.to_crs(config.working_crs)
    streams = _explode_lines(_filter_to_flowing_water(streams))
    best = np.zeros(len(candidates))
    if streams.empty:
        return pd.Series(best, index=candidates.index, dtype=float)

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

    # --- 1. sample every line ------------------------------------------------
    geoms = streams.geometry.values
    flowdirs = (streams["FLOWDIR"].values if "FLOWDIR" in streams.columns
                else np.full(len(streams), None))
    samples = [_line_samples(g) for g in geoms]
    counts = np.array([len(s) for s, _ in samples])
    first = np.concatenate([[0], np.cumsum(counts)[:-1]])
    xy_all = np.vstack([xy for _, xy in samples])

    drainage = np.full(len(xy_all), np.nan)
    for i, (s, xy) in enumerate(samples):
        sl = slice(first[i], first[i] + counts[i])
        if drainage_grid is not None:
            drainage[sl] = _drainage_profile(drainage_grid, s, xy, geoms[i].length, flowdirs[i])
        miss = np.isnan(drainage[sl])
        if miss.any():  # no flow-accumulation read here: segment-length proxy
            drainage[sl] = np.where(miss, _estimate_drainage_area(streams.iloc[i], streams),
                                    drainage[sl])

    z_all = np.full(len(xy_all), np.nan)
    dem_src = _open_dem(dem_path)
    if dem_src is not None:
        try:
            z_all = _channel_elevations(dem_src, xy_all)
        except Exception as exc:
            logger.warning("Hydro: could not read DEM %s (%s: %s); head falls back to "
                           "stream MINZ/MAXZ or a 2%% slope guess",
                           dem_path, type(exc).__name__, exc)
        finally:
            dem_src.close()
    half = int(round(HYDRO_HEAD_SMOOTH_M / HYDRO_PROFILE_STEP_M))
    for i in range(len(streams)):
        sl = slice(first[i], first[i] + counts[i])
        z_all[sl] = _running_median(z_all[sl], half)

    # --- 2. orient downstream, chain into the downstream line ----------------
    order = []
    for i in range(len(streams)):
        idx = np.arange(first[i], first[i] + counts[i])
        order.append(idx[::-1] if _flows_reversed(z_all[idx], flowdirs[i]) else idx)
    succ = _downstream_successors(streams, drainage[first])
    has_dem = np.array([bool(np.isfinite(z_all[o]).any()) for o in order])

    # --- 3. intake/outfall pairs ---------------------------------------------
    ks = sorted({int(round(sep / HYDRO_PROFILE_STEP_M)) for sep in HYDRO_PAIR_SEPARATIONS})
    point_power = np.zeros(len(xy_all))
    for i in np.flatnonzero(has_dem):
        ext = _downstream_path(order, succ, i, max(ks))
        z = z_all[ext]
        for k in ks:
            power = _pair_powers(z, np.isfinite(z), drainage[ext], len(order[i]), k)
            a = np.flatnonzero(power > 0)
            if len(a):
                np.maximum.at(point_power, ext[a], power[a])
                np.maximum.at(point_power, ext[a + k], power[a])

    # --- 4. best pair end within the buffer of each cell ---------------------
    min_scoring = min(low for low, _, score in HYDRO_POWER_THRESHOLDS if score > 0)
    pts = np.flatnonzero(point_power >= min_scoring)
    if len(pts):
        cell_tree = STRtree(candidates.geometry.values)
        pt_idx, cell_idx = cell_tree.query(
            shapely.points(xy_all[pts]), predicate="dwithin", distance=STREAM_BUFFER_M)
        np.maximum.at(best, cell_idx, point_power[pts][pt_idx])

    # --- lines with no DEM coverage: per-cell fallback head ------------------
    no_dem = np.flatnonzero(~has_dem)
    if len(no_dem):
        logger.info("Hydro: %d of %d stream lines have no DEM coverage; using "
                    "fallback head for them", len(no_dem), len(streams))
        # Per-cell elevation range: a fallback head only. Its failure must not
        # switch the DEM off for the lines it does cover.
        cell_zonal = _cell_elevation_ranges(candidates, dem_path) if dem_path.exists() else None
        best = np.maximum(best, _fallback_powers(
            candidates, streams.iloc[no_dem], first[no_dem], drainage, cell_zonal))

    scores = [band_score(p, HYDRO_POWER_THRESHOLDS) for p in best]
    return pd.Series(scores, index=candidates.index, dtype=float)


def _downstream_path(order: list, succ: np.ndarray, i: int, reach: int) -> np.ndarray:
    """Sample indices of line i (upstream first) followed by up to `reach`
    samples of the lines it flows into."""
    parts = [order[i]]
    n_more = 0
    j, seen = succ[i], {i}
    while j >= 0 and j not in seen and n_more < reach:
        parts.append(order[j])
        n_more += len(order[j])
        seen.add(j)
        j = succ[j]
    return np.concatenate(parts)


def _fallback_powers(candidates: gpd.GeoDataFrame, lines: gpd.GeoDataFrame,
                     line_first: np.ndarray, drainage: np.ndarray,
                     cell_zonal: dict | None) -> np.ndarray:
    """Best power per cell from lines the DEM doesn't cover at all.

    Drainage is read at the line point nearest the cell; head is the cell's
    own elevation range (lines >= 100 m), else the line's MAXZ - MINZ, else a
    2% slope guess over its length.
    """
    out = np.zeros(len(candidates))
    tree = STRtree(lines.geometry.values)
    for pos, (idx, geom) in enumerate(zip(candidates.index, candidates.geometry.values)):
        for li in tree.query(geom.buffer(STREAM_BUFFER_M), predicate="intersects"):
            line = lines.iloc[li]
            lg = line.geometry
            n = int(np.floor(lg.length / HYDRO_PROFILE_STEP_M + 1e-9)) + 1
            j = int(np.clip(np.rint(lg.project(geom.centroid) / HYDRO_PROFILE_STEP_M), 0, n - 1))
            area = float(drainage[line_first[li] + j])
            if area < MIN_DRAINAGE_AREA_KM2:
                continue
            head = 0.0
            if cell_zonal is not None and lg.length >= 100:
                cell_range = cell_zonal.get(idx)
                if cell_range is not None and cell_range >= MIN_HEAD_M:
                    head = cell_range
            if head < MIN_HEAD_M:
                minz, maxz = line.get("MINZ"), line.get("MAXZ")
                if pd.notna(minz) and pd.notna(maxz) and abs(maxz - minz) > 0.1:
                    head = abs(maxz - minz)
                else:
                    head = lg.length * 0.02  # 2% slope proxy
            if head < MIN_HEAD_M:
                continue
            out[pos] = max(out[pos], _estimate_power(_estimate_flow_rate(area), head))
    return out

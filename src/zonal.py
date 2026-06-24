"""Fast vectorized zonal statistics for a large set of polygons.

Rasterizes all polygons to a single integer-label grid in one pass, then
computes per-zone statistics with numpy bincount / scipy.ndimage. This replaces
per-geometry `rasterstats.zonal_stats` (and per-cell windowed-read loops), which
are O(n_cells) slow — minutes for 40k cells over a 100M-pixel raster vs seconds
here. Pixel assignment uses pixel-center containment (all_touched=False), the
same convention as rasterstats default.
"""

import logging

import numpy as np
import rasterio
from rasterio.features import rasterize

logger = logging.getLogger("property_finder")


def grid_zonal_stats(
    geoms,
    raster_path,
    stats=("mean",),
    nodata=None,
    frac_range=None,
    band: int = 1,
) -> dict:
    """Per-polygon zonal stats, returned positionally (one value per input geom).

    Args:
        geoms: a GeoSeries (or sequence of shapely geometries) in the raster CRS.
        stats: any of "mean", "min", "max", "range", "frac", "count".
        nodata: override the raster's nodata; excluded from all stats.
        frac_range: (lo, hi) for the "frac" stat — fraction of valid pixels with
            lo <= value <= hi (e.g. south-facing aspect share).

    Returns a dict mapping stat name -> np.ndarray of length len(geoms). Empty
    zones get NaN (or 0.0 for "frac"/"count").
    """
    geom_list = list(getattr(geoms, "values", geoms))
    n = len(geom_list)

    with rasterio.open(raster_path) as src:
        arr = src.read(band)
        transform = src.transform
        rnodata = src.nodata if nodata is None else nodata
        shape = arr.shape

    # Label raster: zone i -> i+1 (0 = background). int32 supports >2B zones.
    labels = rasterize(
        ((g, i + 1) for i, g in enumerate(geom_list)),
        out_shape=shape,
        transform=transform,
        fill=0,
        dtype=np.int32,
        all_touched=False,
    )

    valid = labels > 0
    if rnodata is not None and not (isinstance(rnodata, float) and np.isnan(rnodata)):
        valid &= arr != rnodata
    elif rnodata is not None:
        valid &= ~np.isnan(arr)

    lab = labels[valid]
    val = arr[valid].astype("float64")

    counts = np.bincount(lab, minlength=n + 1)[1:]
    nonempty = counts > 0
    out: dict = {}

    if "count" in stats:
        out["count"] = counts

    if "mean" in stats:
        sums = np.bincount(lab, weights=val, minlength=n + 1)[1:]
        with np.errstate(invalid="ignore", divide="ignore"):
            out["mean"] = np.where(nonempty, sums / np.where(counts == 0, 1, counts), np.nan)

    if any(s in stats for s in ("min", "max", "range")):
        from scipy import ndimage

        idx = np.arange(1, n + 1)
        mx = np.asarray(ndimage.maximum(val, labels=lab, index=idx), dtype="float64")
        mn = np.asarray(ndimage.minimum(val, labels=lab, index=idx), dtype="float64")
        if "max" in stats:
            out["max"] = np.where(nonempty, mx, np.nan)
        if "min" in stats:
            out["min"] = np.where(nonempty, mn, np.nan)
        if "range" in stats:
            out["range"] = np.where(nonempty, mx - mn, np.nan)

    if "frac" in stats:
        if frac_range is None:
            raise ValueError("frac stat requires frac_range=(lo, hi)")
        lo, hi = frac_range
        in_range = ((val >= lo) & (val <= hi)).astype("float64")
        s = np.bincount(lab, weights=in_range, minlength=n + 1)[1:]
        with np.errstate(invalid="ignore", divide="ignore"):
            out["frac"] = np.where(nonempty, s / np.where(counts == 0, 1, counts), 0.0)

    return out

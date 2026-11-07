"""Confidence score computation and banding."""

import logging

import geopandas as gpd
import numpy as np
import pandas as pd

from src.config import Config
from src.constants import (
    ACCESS_FLAG_THRESHOLD,
    COASTAL_LOW_ELEVATION_SCORES,
    CONFIDENCE_BANDS,
    CONFIDENCE_DEDUCTIONS,
    FLAG_ACCESS_UNVERIFIED,
    FLAG_COASTAL_LOW_ELEVATION,
    FLAG_HYDRO_LOW_CONFIDENCE,
    FLAG_SOLAR_LOW_CONFIDENCE,
    FLAG_WIND_PROXY_ONLY,
)

logger = logging.getLogger("property_finder")

# Per-cell flag deductions, subtracted from the confidence of each cell that
# carries the flag, on top of any global data-quality deductions: flag ->
# the config.confidence_deductions key the amount is read from (falling back
# to src.constants.CONFIDENCE_DEDUCTIONS). Only access varies per cell; the
# other flags restate a global data gap that is already deducted once.
PER_CELL_DEDUCTIONS = {
    FLAG_ACCESS_UNVERIFIED: "no_road_evidence_200m",  # no road evidence within 200m
}

# Cell flags in the order they are listed in ``flags`` (SPEC §2.1 order).
FLAG_ORDER = (
    FLAG_ACCESS_UNVERIFIED,
    FLAG_COASTAL_LOW_ELEVATION,
    FLAG_HYDRO_LOW_CONFIDENCE,
    FLAG_SOLAR_LOW_CONFIDENCE,
    FLAG_WIND_PROXY_ONLY,
)

# Score columns that an open-area / solar estimate shows up in.
_SOLAR_SCORE_COLS = ("score_open_ground", "score_solar", "score_buildable")


def _deduction(deductions: dict, key: str) -> float | None:
    """``deductions[key]``, else the src.constants default, else None."""
    amount = deductions.get(key, CONFIDENCE_DEDUCTIONS.get(key))
    return None if amount is None else float(amount)


def _cell_flag_masks(
    candidates: gpd.GeoDataFrame, data_flags: dict[str, bool],
) -> list[tuple[pd.Series, str]]:
    """(mask, flag) for each SPEC §2.1 cell flag, in ``FLAG_ORDER``.

    - ``access_unverified``: access score below 50 (no road or civic address
      within 200 m).
    - ``coastal_low_elevation``: no flood layer (``no_flood_data``) and the
      cell's elevation score is one of the low-coastal bands (mean under
      20 m; ``COASTAL_LOW_ELEVATION_SCORES``).
    - ``hydro_low_confidence``: hydro rests on the drainage proxy
      (``hydro_drainage_proxy_only``) or a 20 m DEM (``hydro_20m_dem``).
    - ``solar_low_confidence``: the open-area mask lacks land cover or
      buildings (``incomplete_land_cover_mask``).
    - ``wind_exposure_proxy_only``: wind from terrain exposure, not a wind
      raster (``wind_proxy_only``).
    """
    nan = pd.Series(np.nan, index=candidates.index)

    def scored(col: str) -> pd.Series:
        return pd.notna(candidates.get(col, nan))

    access = candidates.get("score_access", nan)
    elevation = candidates.get("score_elevation", nan)
    solar_scored = pd.Series(False, index=candidates.index)
    for col in _SOLAR_SCORE_COLS:
        solar_scored |= scored(col)
    masks = {
        FLAG_ACCESS_UNVERIFIED: pd.notna(access) & (access < ACCESS_FLAG_THRESHOLD),
        FLAG_COASTAL_LOW_ELEVATION: (
            scored("score_elevation") & elevation.isin(COASTAL_LOW_ELEVATION_SCORES)
            & bool(data_flags.get("no_flood_data"))
        ),
        FLAG_HYDRO_LOW_CONFIDENCE: scored("score_hydro") & bool(
            data_flags.get("hydro_drainage_proxy_only") or data_flags.get("hydro_20m_dem")),
        FLAG_SOLAR_LOW_CONFIDENCE: solar_scored & bool(data_flags.get("incomplete_land_cover_mask")),
        FLAG_WIND_PROXY_ONLY: scored("score_wind") & bool(data_flags.get("wind_proxy_only")),
    }
    return [(masks[flag], flag) for flag in FLAG_ORDER]


def compute_confidence(
    candidates: gpd.GeoDataFrame,
    config: Config,
    data_flags: dict[str, bool] | None = None,
) -> gpd.GeoDataFrame:
    """Compute confidence score and band for each cell.

    Starts at 100, subtracts penalties based on data quality flags,
    clamps to max(0, score). Assigns a band: high/medium/low.

    data_flags: dict of flag_name -> bool indicating which global data issues apply.
    Per-cell flags (e.g., access_unverified) are computed from cell scores.
    """
    candidates = candidates.copy()
    if data_flags is None:
        data_flags = {}

    deductions = config.confidence_deductions

    # Start with base confidence
    confidence = pd.Series(100.0, index=candidates.index)

    # Apply global data-quality deductions
    for flag, applies in data_flags.items():
        amount = _deduction(deductions, flag) if applies else None
        if amount is not None:
            confidence -= amount
            logger.info("Confidence deduction: %s (-%g)", flag, amount)

    # Per-cell flags and deductions — vectorized. A flag only goes on a cell
    # that has a score for the criterion it is about: a missing column (the
    # criterion is disabled) or a nulled one (the cell is excluded) is NaN,
    # so pd.notna() is False there.
    flag_masks = _cell_flag_masks(candidates, data_flags)

    for mask, flag in flag_masks:
        key = PER_CELL_DEDUCTIONS.get(flag)
        amount = _deduction(deductions, key) if key else None
        if amount:
            confidence = confidence.where(~mask, confidence - amount)

    # Build the semicolon-joined flags string per cell via a fold: at each step,
    # concatenate with "; " only where both sides are non-empty.
    flags_col = pd.Series("", index=candidates.index, dtype="object")
    for mask, flag in flag_masks:
        addition = pd.Series(np.where(mask, flag, ""), index=candidates.index)
        flags_col = pd.Series(
            np.where(
                (flags_col != "") & (addition != ""), flags_col + "; " + addition,
                np.where(flags_col != "", flags_col, addition),
            ),
            index=candidates.index,
        )
    candidates["flags"] = flags_col
    logger.info("Cell flags: %s", ", ".join(
        f"{flag}={int(mask.sum())}" for mask, flag in flag_masks))

    # Clamp confidence
    confidence = confidence.clip(lower=0)
    candidates["confidence"] = confidence

    # Assign bands — reversed so the FIRST matching entry in CONFIDENCE_BANDS
    # wins (matches the old break-on-first-match loop) even if a custom config
    # ever supplies overlapping ranges; later assignments overwrite earlier ones.
    bands = pd.Series("low", index=candidates.index, dtype="object")
    for low, high, label in reversed(CONFIDENCE_BANDS):
        bands[(confidence >= low) & (confidence <= high)] = label
    candidates["confidence_band"] = bands

    # Null out confidence for excluded cells (no status column = all eligible)
    excluded = candidates.get("status", pd.Series("eligible", index=candidates.index)) == "excluded"
    if excluded.any():
        candidates.loc[excluded, "confidence"] = None
        candidates.loc[excluded, "confidence_band"] = None

    logger.info(
        "Confidence bands: high=%d, medium=%d, low=%d",
        (candidates["confidence_band"] == "high").sum(),
        (candidates["confidence_band"] == "medium").sum(),
        (candidates["confidence_band"] == "low").sum(),
    )
    return candidates

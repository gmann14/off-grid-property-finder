"""Confidence score computation and banding."""

import logging

import geopandas as gpd
import numpy as np
import pandas as pd

from src.config import Config
from src.constants import (
    ACCESS_FLAG_THRESHOLD,
    CONFIDENCE_BANDS,
    CONFIDENCE_DEDUCTIONS,
    FLAG_ACCESS_UNVERIFIED,
    FLAG_HYDRO_LOW_CONFIDENCE,
)

logger = logging.getLogger("property_finder")

# Per-cell flag deduction amounts.  These are subtracted from the
# confidence score for each individual cell that triggers the flag,
# in addition to any global data-quality deductions.
PER_CELL_DEDUCTIONS = {
    FLAG_ACCESS_UNVERIFIED: 15,   # no road evidence within 200m
    FLAG_HYDRO_LOW_CONFIDENCE: 10,  # hydro score is zero / no stream data
}


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
        if applies and flag in deductions:
            confidence -= deductions[flag]
            logger.info("Confidence deduction: %s (-%d)", flag, deductions[flag])

    # Per-cell flags and deductions — vectorized (was a per-row iterrows loop).
    # access_score/hydro_score default to an all-NaN Series when the column is
    # absent, so pd.notna() below is False everywhere, matching the old
    # cell.get(...) -> None -> pd.notna(None) -> False behavior.
    access_score = candidates.get("score_access", pd.Series(np.nan, index=candidates.index))
    hydro_score = candidates.get("score_hydro", pd.Series(np.nan, index=candidates.index))

    flag_masks = [
        (pd.notna(access_score) & (access_score < ACCESS_FLAG_THRESHOLD), FLAG_ACCESS_UNVERIFIED),
        (pd.notna(hydro_score) & (hydro_score == 0), FLAG_HYDRO_LOW_CONFIDENCE),
    ]

    for mask, flag in flag_masks:
        if flag in PER_CELL_DEDUCTIONS:
            confidence = confidence.where(~mask, confidence - PER_CELL_DEDUCTIONS[flag])

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

    # Null out confidence for excluded cells
    excluded = candidates.get("status") == "excluded"
    if excluded is not None and excluded.any():
        candidates.loc[excluded, "confidence"] = None
        candidates.loc[excluded, "confidence_band"] = None

    logger.info(
        "Confidence bands: high=%d, medium=%d, low=%d",
        (candidates["confidence_band"] == "high").sum(),
        (candidates["confidence_band"] == "medium").sum(),
        (candidates["confidence_band"] == "low").sum(),
    )
    return candidates

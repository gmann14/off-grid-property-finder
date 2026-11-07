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

# Per-cell flag deductions, subtracted from the confidence of each cell that
# carries the flag, on top of any global data-quality deductions. A string is
# the config.confidence_deductions key the amount is read from (falling back
# to src.constants.CONFIDENCE_DEDUCTIONS); a number is a fixed amount.
PER_CELL_DEDUCTIONS = {
    FLAG_ACCESS_UNVERIFIED: "no_road_evidence_200m",  # no road evidence within 200m
    FLAG_HYDRO_LOW_CONFIDENCE: 10,  # hydro score is zero / no stream data
}


def _deduction(deductions: dict, key: str) -> float | None:
    """``deductions[key]``, else the src.constants default, else None."""
    amount = deductions.get(key, CONFIDENCE_DEDUCTIONS.get(key))
    return None if amount is None else float(amount)


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
        amount = PER_CELL_DEDUCTIONS.get(flag)
        if isinstance(amount, str):
            amount = _deduction(deductions, amount)
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

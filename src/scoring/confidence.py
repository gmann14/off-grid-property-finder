"""Confidence score computation and banding."""

import logging

import geopandas as gpd
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

    # Per-cell deductions
    flags_col = []
    for idx, cell in candidates.iterrows():
        cell_flags = []

        # Access unverified flag
        access_score = cell.get("score_access")
        if access_score is not None and access_score < ACCESS_FLAG_THRESHOLD:
            cell_flags.append(FLAG_ACCESS_UNVERIFIED)

        # Hydro low confidence flag
        hydro_score = cell.get("score_hydro")
        if hydro_score is not None and hydro_score == 0:
            cell_flags.append(FLAG_HYDRO_LOW_CONFIDENCE)

        flags_col.append("; ".join(cell_flags) if cell_flags else "")

    candidates["flags"] = flags_col

    # Clamp confidence
    confidence = confidence.clip(lower=0)
    candidates["confidence"] = confidence

    # Assign bands
    bands = []
    for conf in confidence:
        band = "low"
        for low, high, label in CONFIDENCE_BANDS:
            if low <= conf <= high:
                band = label
                break
        bands.append(band)
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

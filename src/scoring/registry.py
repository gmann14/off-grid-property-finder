"""Metric registry — pluggable scoring functions with weight renormalization."""

import logging
from typing import Callable

import geopandas as gpd
import numpy as np

from src.config import Config
from src.constants import WIND_WORTH_IT_OPEN_GROUND_MAX, WIND_WORTH_IT_WIND_MIN

logger = logging.getLogger("property_finder")

# Type for a scoring function: (candidates, config) -> Series of 0-100 scores
ScoringFunc = Callable[[gpd.GeoDataFrame, Config], gpd.pd.Series]

_REGISTRY: dict[str, ScoringFunc] = {}


def register(name: str) -> Callable[[ScoringFunc], ScoringFunc]:
    """Decorator to register a scoring function."""
    def wrapper(func: ScoringFunc) -> ScoringFunc:
        _REGISTRY[name] = func
        return func
    return wrapper


def get_scorer(name: str) -> ScoringFunc:
    if name not in _REGISTRY:
        raise KeyError(f"Unknown scoring metric: {name}. Available: {list(_REGISTRY.keys())}")
    return _REGISTRY[name]


def available_scorers() -> list[str]:
    return list(_REGISTRY.keys())


def compute_composite_score(
    candidates: gpd.GeoDataFrame,
    config: Config,
) -> gpd.GeoDataFrame:
    """Run all enabled scorers and compute weighted composite score.

    Adds individual score columns (score_<name>) and a composite 'score' column.
    Excluded cells (status == 'excluded') get score = NaN.
    """
    weights = config.enabled_weights()
    candidates = candidates.copy()

    for name, weight in weights.items():
        scorer = get_scorer(name)
        col = f"score_{name}"
        try:
            candidates[col] = scorer(candidates, config)
            logger.info("Scored %s: mean=%.1f, median=%.1f",
                        name, candidates[col].mean(), candidates[col].median())
        except Exception:
            logger.exception("Scorer '%s' failed; assigning 0", name)
            candidates[col] = 0.0

    # Compute weighted composite (compensatory — strengths offset weaknesses)
    score_cols = [f"score_{name}" for name in weights]
    weight_values = [weights[name] / 100.0 for name in weights]

    composite = sum(
        candidates[col] * w for col, w in zip(score_cols, weight_values)
    )
    candidates["score"] = composite

    # All-rounder (conjunctive) score: weighted geometric mean of the sub-scores.
    # Unlike the composite, a single weak criterion drags this down hard — use it
    # to hunt true do-it-all "perfect candidate" parcels rather than one-trick sites.
    total_w = sum(weight_values) or 1.0
    ln_sum = sum(
        w * np.log(candidates[col].clip(lower=1.0))
        for col, w in zip(score_cols, weight_values)
    )
    candidates["score_allrounder"] = np.exp(ln_sum / total_w)

    # "Wind worth it here": meaningful wind resource AND limited room for more
    # solar (your logic — wind only pays off when solar can't cover the gap).
    if "score_wind" in candidates.columns and "score_open_ground" in candidates.columns:
        candidates["wind_worth_it"] = (
            (candidates["score_wind"] >= WIND_WORTH_IT_WIND_MIN)
            & (candidates["score_open_ground"] <= WIND_WORTH_IT_OPEN_GROUND_MAX)
        )

    # Null out excluded cells
    excluded = candidates.get("status") == "excluded"
    if excluded is not None and excluded.any():
        candidates.loc[excluded, "score"] = None
        candidates.loc[excluded, "score_allrounder"] = None
        if "wind_worth_it" in candidates.columns:
            candidates.loc[excluded, "wind_worth_it"] = None
        for col in score_cols:
            candidates.loc[excluded, col] = None

    logger.info(
        "Composite scores: eligible mean=%.1f, eligible median=%.1f",
        candidates["score"].mean(),
        candidates["score"].median(),
    )
    return candidates

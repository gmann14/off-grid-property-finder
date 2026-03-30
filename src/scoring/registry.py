"""Metric registry — pluggable scoring functions with weight renormalization."""

import logging
from typing import Callable

import geopandas as gpd

from src.config import Config

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

    # Compute weighted composite
    score_cols = [f"score_{name}" for name in weights]
    weight_values = [weights[name] / 100.0 for name in weights]

    composite = sum(
        candidates[col] * w for col, w in zip(score_cols, weight_values)
    )
    candidates["score"] = composite

    # Null out excluded cells
    excluded = candidates.get("status") == "excluded"
    if excluded is not None and excluded.any():
        candidates.loc[excluded, "score"] = None
        for col in score_cols:
            candidates.loc[excluded, col] = None

    logger.info(
        "Composite scores: eligible mean=%.1f, eligible median=%.1f",
        candidates["score"].mean(),
        candidates["score"].median(),
    )
    return candidates

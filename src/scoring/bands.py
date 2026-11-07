"""Band lookup shared by the scorers' threshold tables in src.constants."""

import math


def band_score(value: float, thresholds: list[tuple]) -> int:
    """Score of the first band with ``low <= value < high``; 0 if none matches.

    A top band that is open-ended (``high`` is +inf) also takes +inf itself,
    so an unbounded value (e.g. infinite acreage or power) lands in the top
    band instead of falling through every half-open range to 0. NaN matches
    no band and scores 0; callers that want a neutral score for missing data
    handle NaN before calling.
    """
    for low, high, score in thresholds:
        if low <= value < high or (value == high and math.isinf(high) and high > 0):
            return score
    return 0

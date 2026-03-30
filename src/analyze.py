"""Analyze score distributions from scored output."""

import logging
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

from src.config import Config

logger = logging.getLogger("property_finder")

SCORE_COLUMNS = [
    "score_hydro",
    "score_solar",
    "score_elevation",
    "score_access",
    "score_buildable",
    "score",
]

CONFIDENCE_COLUMN = "confidence"
CONFIDENCE_BAND_COLUMN = "confidence_band"

PERCENTILES = [10, 25, 50, 75, 90]
HISTOGRAM_BINS = 10
HISTOGRAM_WIDTH = 40  # max bar width in characters


def _load_scored_cells(output_dir: Path) -> gpd.GeoDataFrame:
    """Load scored_cells.gpkg from the output directory."""
    path = output_dir / "scored_cells.gpkg"
    if not path.exists():
        raise FileNotFoundError(
            f"scored_cells.gpkg not found at {path}. Run 'score' first."
        )
    return gpd.read_file(path)


def _format_stats(series: pd.Series) -> str:
    """Format basic statistics for a score series."""
    valid = series.dropna()
    if valid.empty:
        return "  No valid data\n"

    lines = []
    lines.append(
        f"  Count: {len(valid):,}  |  "
        f"Min: {valid.min():.1f}  |  Max: {valid.max():.1f}  |  "
        f"Mean: {valid.mean():.1f}  |  Median: {valid.median():.1f}"
    )

    pct_values = np.percentile(valid, PERCENTILES)
    pct_parts = [f"P{p}: {v:.1f}" for p, v in zip(PERCENTILES, pct_values)]
    lines.append(f"  Percentiles: {' | '.join(pct_parts)}")

    count_100 = int((valid == 100).sum())
    count_0 = int((valid == 0).sum())
    total = len(valid)
    lines.append(
        f"  Cells at 100: {count_100:,} ({count_100 / total * 100:.1f}%)  |  "
        f"Cells at 0: {count_0:,} ({count_0 / total * 100:.1f}%)"
    )

    return "\n".join(lines) + "\n"


def _format_histogram(series: pd.Series, bins: int = HISTOGRAM_BINS) -> str:
    """Format a text-based histogram for a score series."""
    valid = series.dropna()
    if valid.empty:
        return "  No valid data\n"

    counts, edges = np.histogram(valid, bins=bins, range=(0, 100))
    max_count = counts.max() if counts.max() > 0 else 1

    lines = []
    for i, count in enumerate(counts):
        lo = edges[i]
        hi = edges[i + 1]
        bar_len = int(count / max_count * HISTOGRAM_WIDTH)
        bar = "#" * bar_len
        label = f"  {lo:5.0f}-{hi:5.0f}"
        lines.append(f"{label} | {bar:<{HISTOGRAM_WIDTH}} {count:,}")

    return "\n".join(lines) + "\n"


def _format_confidence_bands(gdf: gpd.GeoDataFrame) -> str:
    """Format confidence band counts."""
    if CONFIDENCE_BAND_COLUMN not in gdf.columns:
        return "  No confidence_band column found\n"

    valid = gdf[CONFIDENCE_BAND_COLUMN].dropna()
    if valid.empty:
        return "  No valid data\n"

    total = len(valid)
    lines = []
    for band in ["high", "medium", "low"]:
        count = int((valid == band).sum())
        lines.append(f"  {band}: {count:,} ({count / total * 100:.1f}%)")

    return "\n".join(lines) + "\n"


def run_analyze(config: Config, logger: logging.Logger) -> str:
    """Analyze score distributions and return formatted report.

    Returns the full report as a string (also printed to stdout).
    """
    gdf = _load_scored_cells(config.paths.output)
    logger.info("Loaded %d cells from scored_cells.gpkg", len(gdf))

    sections = []

    # Score columns
    for col in SCORE_COLUMNS:
        if col not in gdf.columns:
            sections.append(f"--- {col} ---\n  Column not found in output\n")
            continue

        sections.append(f"--- {col} ---")
        sections.append(_format_stats(gdf[col]))
        sections.append(_format_histogram(gdf[col]))

    # Confidence score
    if CONFIDENCE_COLUMN in gdf.columns:
        sections.append(f"--- {CONFIDENCE_COLUMN} ---")
        sections.append(_format_stats(gdf[CONFIDENCE_COLUMN]))
        sections.append(_format_histogram(gdf[CONFIDENCE_COLUMN]))
        sections.append("  Confidence bands:")
        sections.append(_format_confidence_bands(gdf))
    else:
        sections.append(f"--- {CONFIDENCE_COLUMN} ---\n  Column not found in output\n")

    report = "\n".join(sections)
    print(report)
    return report

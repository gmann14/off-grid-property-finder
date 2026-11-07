"""Rebuild the committed ``results/`` snapshot from ``output/ranked_parcels.csv``.

``results/`` is what other tools import (it is committed; ``output/`` is not), so
it must be rebuildable by anyone from a ``score`` run. Three files:

- ``ranked_pids.csv``: every ranked parcel, best first (ties by PID, as the
  export orders them), with a Google Maps link;
- ``top_land_only.csv``: the best ``TOP_LAND_ONLY_N`` land-only parcels;
- ``severance_candidates.csv``: every severance candidate.
"""

import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger("property_finder")

SNAPSHOT_COLUMNS = [
    "rank", "PID", "score", "cell_score", "n_cells", "area_acres", "land_acres",
    "water_share", "parcel_type", "n_buildings", "severance_candidate",
    "latitude", "longitude",
]
TOP_LAND_ONLY_N = 50
MAPS_URL = "https://www.google.com/maps?q={lat},{lon}"


class SnapshotError(RuntimeError):
    """The ranked export is missing, empty, incomplete or older than the run."""


def _as_bool(series: pd.Series) -> pd.Series:
    """CSV booleans arrive as bool, or as the strings "True"/"False"."""
    if series.dtype == bool:
        return series
    return series.astype(str).str.strip().str.lower().eq("true")


def build_snapshot(ranked_csv: Path, out_dir: Path,
                   scored_parcels: Path | None = None) -> dict[str, int]:
    """Write the three snapshot files to ``out_dir`` and return row counts.

    Raises SnapshotError when ``ranked_csv`` is missing, empty or lacks a
    snapshot column, or when ``scored_parcels`` (the GPKG ``score`` writes just
    before the CSV) is newer than it, which means the last run stopped part-way.
    """
    ranked_csv, out_dir = Path(ranked_csv), Path(out_dir)
    if not ranked_csv.exists():
        raise SnapshotError(f"{ranked_csv} not found: run `python -m src score` first")
    if scored_parcels is not None and Path(scored_parcels).exists():
        if Path(scored_parcels).stat().st_mtime > ranked_csv.stat().st_mtime:
            raise SnapshotError(
                f"{scored_parcels} is newer than {ranked_csv}: the last `score` run "
                "did not finish its export. Re-run `python -m src score`."
            )
    try:
        ranked = pd.read_csv(ranked_csv, dtype={"PID": str})
    except pd.errors.EmptyDataError as e:
        raise SnapshotError(f"{ranked_csv} is empty") from e
    missing = [c for c in SNAPSHOT_COLUMNS if c not in ranked.columns]
    if missing:
        raise SnapshotError(f"{ranked_csv} lacks columns {missing}")
    if ranked.empty:
        raise SnapshotError(f"{ranked_csv} has no ranked parcels")

    # Stable sort keeps the export's PID order inside tied ranks.
    out = ranked.sort_values("rank", kind="stable")[SNAPSHOT_COLUMNS].copy()
    out["maps_link"] = [
        MAPS_URL.format(lat=round(la, 6), lon=round(lo, 6))
        if pd.notna(la) and pd.notna(lo) else ""
        for la, lo in zip(out["latitude"], out["longitude"])
    ]

    out_dir.mkdir(parents=True, exist_ok=True)
    land_only = out[out["parcel_type"] == "land_only"].head(TOP_LAND_ONLY_N)
    severance = out[_as_bool(out["severance_candidate"])]
    out.to_csv(out_dir / "ranked_pids.csv", index=False)
    land_only.to_csv(out_dir / "top_land_only.csv", index=False)
    severance.to_csv(out_dir / "severance_candidates.csv", index=False)

    counts = {"ranked": len(out), "top_land_only": len(land_only),
              "severance_candidates": len(severance)}
    logger.info(
        "Snapshot in %s: %d ranked parcels, top %d land-only, %d severance candidates",
        out_dir, counts["ranked"], counts["top_land_only"], counts["severance_candidates"],
    )
    return counts

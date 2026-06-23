"""Export scored results to CSV and GeoJSON."""

import logging
from pathlib import Path

import geopandas as gpd

logger = logging.getLogger("property_finder")

# Columns to include in CSV export
CSV_COLUMNS = [
    "status", "score", "score_allrounder", "rank",
    "score_hydro", "score_access", "score_open_ground", "score_wind", "score_elevation",
    "score_solar", "score_buildable",  # legacy criteria, present only if enabled
    "wind_worth_it",
    "confidence", "confidence_band", "flags", "exclusion_reasons",
]

# Columns (in order) for the ranked-parcel CSV — the PID candidate list
PARCEL_CSV_COLUMNS = [
    "PID", "AAN", "rank", "score", "cell_score", "size_score",
    "n_cells", "area_acres", "parcel_type", "n_buildings", "severance_candidate",
    "flags",
]


def export_results(candidates: gpd.GeoDataFrame, output_dir: Path) -> None:
    """Export scored cells to CSV and GeoJSON."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # CSV (no geometry)
    csv_cols = [c for c in CSV_COLUMNS if c in candidates.columns]
    csv_df = candidates[csv_cols].copy()

    # Add centroid lat/lon for CSV
    centroids = candidates.geometry.centroid
    try:
        centroids_wgs84 = centroids.to_crs("EPSG:4326")
        csv_df["latitude"] = centroids_wgs84.y
        csv_df["longitude"] = centroids_wgs84.x
    except Exception:
        logger.warning("Could not compute WGS84 centroids for CSV export")

    csv_path = output_dir / "scored_cells.csv"
    csv_df.to_csv(csv_path)
    logger.info("Exported CSV: %s (%d rows)", csv_path, len(csv_df))

    # GeoJSON (WGS84 for web mapping)
    try:
        geojson_gdf = candidates.to_crs("EPSG:4326")
        geojson_path = output_dir / "scored_cells.geojson"
        geojson_gdf.to_file(geojson_path, driver="GeoJSON")
        logger.info("Exported GeoJSON: %s", geojson_path)
    except Exception:
        logger.exception("Failed to export GeoJSON")

    # Eligible-only ranked view
    eligible = candidates[candidates.get("status") != "excluded"].copy()
    if not eligible.empty:
        eligible_sorted = eligible.sort_values("rank")
        eligible_csv = eligible_sorted[[c for c in csv_cols if c in eligible_sorted.columns]].copy()
        try:
            eligible_centroids = eligible_sorted.geometry.centroid.to_crs("EPSG:4326")
            eligible_csv["latitude"] = eligible_centroids.y
            eligible_csv["longitude"] = eligible_centroids.x
        except Exception:
            pass
        eligible_path = output_dir / "ranked_eligible.csv"
        eligible_csv.to_csv(eligible_path)
        logger.info("Exported eligible-only CSV: %s (%d rows)", eligible_path, len(eligible_csv))


def export_ranked_parcels(parcels: gpd.GeoDataFrame, output_dir: Path) -> None:
    """Export scored parcels as a ranked PID candidate list (CSV + GeoJSON).

    ``ranked_parcels.csv`` holds only parcels with an assigned score, sorted
    best-first, keyed by PID — this is the Stage B deliverable. The GeoJSON
    keeps geometry for web mapping.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # GeoJSON with geometry (WGS84) — all parcels, scored or not
    try:
        geojson_path = output_dir / "scored_parcels.geojson"
        parcels.to_crs("EPSG:4326").to_file(geojson_path, driver="GeoJSON")
        logger.info("Exported parcels GeoJSON: %s", geojson_path)
    except Exception:
        logger.exception("Failed to export parcels GeoJSON")

    # Ranked CSV — scored parcels only, sorted best-first, with centroid lat/lon
    scored = parcels[parcels["score"].notna()].copy()
    if scored.empty:
        logger.warning("No scored parcels to rank; skipping ranked_parcels.csv")
        return

    sort_col = "rank" if "rank" in scored.columns else "score"
    ascending = sort_col == "rank"
    scored = scored.sort_values(sort_col, ascending=ascending)

    cols = [c for c in PARCEL_CSV_COLUMNS if c in scored.columns]
    csv_df = scored[cols].copy()
    try:
        centroids = scored.geometry.centroid.to_crs("EPSG:4326")
        csv_df["latitude"] = centroids.y
        csv_df["longitude"] = centroids.x
    except Exception:
        logger.warning("Could not compute WGS84 centroids for parcel CSV export")

    csv_path = output_dir / "ranked_parcels.csv"
    csv_df.to_csv(csv_path, index=False)
    logger.info("Exported ranked parcels CSV: %s (%d parcels)", csv_path, len(csv_df))

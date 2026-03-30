"""Export scored results to CSV and GeoJSON."""

import logging
from pathlib import Path

import geopandas as gpd

logger = logging.getLogger("property_finder")

# Columns to include in CSV export
CSV_COLUMNS = [
    "status", "score", "rank",
    "score_hydro", "score_solar", "score_elevation", "score_access", "score_buildable",
    "confidence", "confidence_band", "flags", "exclusion_reasons",
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

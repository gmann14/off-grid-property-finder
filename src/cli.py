"""CLI entrypoint for property-finder."""

import click

from src.config import load_config
from src.logging_config import setup_logging


@click.group()
@click.option("--config", "config_path", default="config.yaml", help="Path to config file")
@click.option("--log-level", default=None, help="Logging level (DEBUG, INFO, WARNING, ERROR)")
@click.pass_context
def cli(ctx: click.Context, config_path: str, log_level: str | None) -> None:
    """Off-Grid Property Finder — rank Nova Scotia land for off-grid suitability."""
    ctx.ensure_object(dict)
    ctx.obj["logger"] = setup_logging(log_level)
    ctx.obj["config"] = load_config(config_path)


@cli.command()
@click.pass_context
def check_data(ctx: click.Context) -> None:
    """Check that required data files are present and readable."""
    from src.check_data import run_check_data

    cfg = ctx.obj["config"]
    logger = ctx.obj["logger"]
    run_check_data(cfg, logger)


@cli.command()
@click.option("--force", is_flag=True, default=False,
              help="Regenerate outputs even if data/processed/ was built from a different config")
@click.pass_context
def ingest(ctx: click.Context, force: bool) -> None:
    """Ingest raw data into standardized formats (GPKG/GeoTIFF)."""
    from src.ingest import run_ingest
    from src.manifest import StaleProcessedDataError

    cfg = ctx.obj["config"]
    logger = ctx.obj["logger"]
    try:
        run_ingest(cfg, logger, force=force)
    except StaleProcessedDataError as e:
        logger.error(str(e))
        raise SystemExit(1)


@cli.command("ingest-parcels")
@click.option(
    "--from-rest", is_flag=True, default=False,
    help="Pull parcels from the NSPRD ArcGIS service instead of data/raw/parcels/",
)
@click.option("--layer", type=int, default=None,
              help="Override the REST parcel layer id (default 0) if it isn't the parcel polygons")
@click.option("--service", default=None,
              help="Override the REST MapServer URL (default: NSPRD PLAN_NSPRD_WM84 via nsgiwa2)")
@click.pass_context
def ingest_parcels_cmd(ctx: click.Context, from_rest: bool, layer: int | None, service: str | None) -> None:
    """Ingest NS property parcels (with PID) for Stage B aggregation."""
    from src.ingest import ingest_parcels

    cfg = ctx.obj["config"]
    logger = ctx.obj["logger"]
    path = ingest_parcels(
        cfg, source="rest" if from_rest else "local", layer_id=layer, base_url=service,
    )
    if path is None:
        logger.error(
            "Parcel ingestion produced no output. Place a parcel file in "
            "data/raw/parcels/ or pass --from-rest."
        )
    else:
        logger.info("Parcels ready: %s. Re-run `score` to produce ranked PIDs.", path)


@cli.command()
@click.option("--force", is_flag=True, default=False,
              help="Regenerate outputs even if data/processed/ was built from a different config")
@click.pass_context
def prepare(ctx: click.Context, force: bool) -> None:
    """Prepare data: clip to study area, generate DEM derivatives, build masks and candidate grid."""
    from src.prepare import run_prepare
    from src.manifest import StaleProcessedDataError

    cfg = ctx.obj["config"]
    logger = ctx.obj["logger"]
    try:
        run_prepare(cfg, logger, force=force)
    except StaleProcessedDataError as e:
        logger.error(str(e))
        raise SystemExit(1)


@cli.command()
@click.option("--limit", type=int, default=None,
              help="Smoke mode: score only the first N cells (fast end-to-end check)")
@click.pass_context
def score(ctx: click.Context, limit: int | None) -> None:
    """Score candidate cells and produce ranked output."""
    from src.score import run_score

    cfg = ctx.obj["config"]
    logger = ctx.obj["logger"]
    run_score(cfg, logger, limit=limit)


@cli.command()
@click.pass_context
def visualize(ctx: click.Context) -> None:
    """Generate an interactive Folium map from scored output."""
    from src.visualize import run_visualize

    cfg = ctx.obj["config"]
    logger = ctx.obj["logger"]
    run_visualize(cfg, logger)


@cli.command()
@click.pass_context
def analyze(ctx: click.Context) -> None:
    """Print score distribution statistics from scored output."""
    from src.analyze import run_analyze

    cfg = ctx.obj["config"]
    logger = ctx.obj["logger"]
    run_analyze(cfg, logger)

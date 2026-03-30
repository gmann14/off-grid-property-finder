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
@click.pass_context
def ingest(ctx: click.Context) -> None:
    """Ingest raw data into standardized formats (GPKG/GeoTIFF)."""
    from src.ingest import run_ingest

    cfg = ctx.obj["config"]
    logger = ctx.obj["logger"]
    run_ingest(cfg, logger)


@cli.command()
@click.pass_context
def prepare(ctx: click.Context) -> None:
    """Prepare data: clip to study area, generate DEM derivatives, build masks and candidate grid."""
    from src.prepare import run_prepare

    cfg = ctx.obj["config"]
    logger = ctx.obj["logger"]
    run_prepare(cfg, logger)


@cli.command()
@click.pass_context
def score(ctx: click.Context) -> None:
    """Score candidate cells and produce ranked output."""
    from src.score import run_score

    cfg = ctx.obj["config"]
    logger = ctx.obj["logger"]
    run_score(cfg, logger)


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

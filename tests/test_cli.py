"""Tests for CLI interface."""

import pytest
from click.testing import CliRunner

from src.cli import cli


@pytest.fixture
def runner():
    return CliRunner()


def test_cli_help(runner):
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Off-Grid Property Finder" in result.output


def test_cli_check_data_help(runner):
    result = runner.invoke(cli, ["check-data", "--help"])
    assert result.exit_code == 0
    assert "Check that required data files" in result.output


def test_cli_prepare_help(runner):
    result = runner.invoke(cli, ["prepare", "--help"])
    assert result.exit_code == 0


def test_cli_score_help(runner):
    result = runner.invoke(cli, ["score", "--help"])
    assert result.exit_code == 0


def test_cli_visualize_help(runner):
    result = runner.invoke(cli, ["visualize", "--help"])
    assert result.exit_code == 0


def test_cli_missing_config(runner, tmp_path):
    result = runner.invoke(cli, ["--config", str(tmp_path / "nope.yaml"), "check-data"])
    assert result.exit_code != 0


def _write_config(tmp_path, hub=30):
    import yaml

    from src.constants import WIND_GWA_LAYERS

    cfg = {
        "study_area": {"bbox": [380000, 4900000, 381000, 4901000], "name": "test"},
        "paths": {"raw": str(tmp_path / "raw"), "processed": str(tmp_path / "processed"),
                  "output": str(tmp_path / "out")},
        "wind": {"hub_height_m": hub, "gwa_layers": dict(WIND_GWA_LAYERS)},
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.dump(cfg))
    return path


def test_cli_score_refuses_stale_wind_with_exit_code_1(runner, tmp_path):
    """Regression: score logged the stale-data error but exited 0, so a script
    chaining `score && visualize` carried on with the old output."""
    from src.config import load_config
    from src.manifest import record_layer

    processed = tmp_path / "processed"
    processed.mkdir()
    (processed / "wind.tif").write_bytes(b"built at 50 m")
    record_layer(load_config(_write_config(tmp_path, hub=50)), "wind")

    result = runner.invoke(cli, ["--config", str(_write_config(tmp_path, hub=30)), "score"])
    assert result.exit_code == 1
    assert not (tmp_path / "out").exists()  # refused before writing anything


def test_cli_score_refuses_unrecorded_wind_with_exit_code_1(runner, tmp_path):
    processed = tmp_path / "processed"
    processed.mkdir()
    (processed / "wind.tif").write_bytes(b"July 100 m file, no record")
    result = runner.invoke(cli, ["--config", str(_write_config(tmp_path)), "score"])
    assert result.exit_code == 1


def test_cli_score_without_a_candidate_grid_exits_1(runner, tmp_path):
    """Regression: score logged "Candidate grid not found" and exited 0, so
    `score && visualize` carried on with the previous run's output."""
    (tmp_path / "processed").mkdir()
    result = runner.invoke(cli, ["--config", str(_write_config(tmp_path)), "score"])
    assert result.exit_code == 1
    assert not (tmp_path / "out").exists()  # refused before writing anything


def test_run_score_without_a_candidate_grid_raises(tmp_path):
    import logging

    from src.config import load_config
    from src.manifest import MissingProcessedDataError
    from src.score import run_score

    (tmp_path / "processed").mkdir()
    with pytest.raises(MissingProcessedDataError, match="candidate_grid.gpkg"):
        run_score(load_config(_write_config(tmp_path)), logging.getLogger("test"))


def test_cli_snapshot_fails_without_a_score_run(runner, tmp_path):
    cfg = _write_config(tmp_path)
    result = runner.invoke(cli, ["--config", str(cfg), "snapshot", "--out", str(tmp_path / "res")])
    assert result.exit_code == 1
    assert not (tmp_path / "res").exists()


def test_cli_snapshot_writes_results(runner, tmp_path):
    from tests.test_snapshot import _ranked

    cfg = _write_config(tmp_path)
    (tmp_path / "out").mkdir()
    _ranked().to_csv(tmp_path / "out" / "ranked_parcels.csv", index=False)
    result = runner.invoke(cli, ["--config", str(cfg), "snapshot", "--out", str(tmp_path / "res")])
    assert result.exit_code == 0, result.output
    assert sorted(p.name for p in (tmp_path / "res").iterdir()) == [
        "ranked_pids.csv", "severance_candidates.csv", "top_land_only.csv"]


def test_cli_visualize_exits_1_without_scored_cells(runner, tmp_path):
    cfg = _write_config(tmp_path)
    result = runner.invoke(cli, ["--config", str(cfg), "visualize"])
    assert result.exit_code == 1


def test_cli_ingest_parcels_exits_1_when_nothing_is_ingested(runner, tmp_path):
    cfg = _write_config(tmp_path)
    result = runner.invoke(cli, ["--config", str(cfg), "ingest-parcels"])
    assert result.exit_code == 1


def test_cli_score_exits_1_when_a_scorer_fails(runner, tmp_path, monkeypatch):
    import src.score
    from src.scoring.registry import ScorerError

    def boom(*_a, **_k):
        raise ScorerError("Scorer 'hydro' failed: boom")

    monkeypatch.setattr(src.score, "run_score", boom)
    cfg = _write_config(tmp_path)
    result = runner.invoke(cli, ["--config", str(cfg), "score"])
    assert result.exit_code == 1
    assert not isinstance(result.exception, ScorerError)   # clean exit, no traceback


def test_cli_check_data_exits_1_when_required_raw_layers_are_missing(runner, tmp_path):
    """Regression: check-data logged MISSING (required) but exited 0, so a
    script chaining `check-data && ingest` ran ingest anyway."""
    result = runner.invoke(cli, ["--config", str(_write_config(tmp_path)), "check-data"])
    assert result.exit_code == 1

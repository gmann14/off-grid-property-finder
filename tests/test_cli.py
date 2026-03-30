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

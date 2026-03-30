"""Tests for config loading and validation."""

import pytest
import yaml

from src.config import Config, StudyArea, load_config
from src.constants import DEFAULT_WEIGHTS


def test_load_config_from_yaml(tmp_path):
    config_data = {
        "study_area": {"bbox": [1, 2, 3, 4], "name": "test"},
        "cell_size_m": 500,
        "weights": {"hydro": 50, "solar": 50},
        "enabled_criteria": ["hydro", "solar"],
    }
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml.dump(config_data))

    cfg = load_config(config_file)
    assert cfg.study_area.bbox == (1, 2, 3, 4)
    assert cfg.cell_size_m == 500
    assert cfg.weights == {"hydro": 50, "solar": 50}


def test_load_config_missing_file():
    with pytest.raises(FileNotFoundError):
        load_config("/nonexistent/config.yaml")


def test_enabled_criterion_without_weight(tmp_path):
    config_data = {
        "study_area": {"bbox": [1, 2, 3, 4]},
        "weights": {"hydro": 100},
        "enabled_criteria": ["hydro", "solar"],  # solar has no weight
    }
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml.dump(config_data))

    with pytest.raises(ValueError, match="solar"):
        load_config(config_file)


def test_enabled_weights_renormalization():
    cfg = Config(
        study_area=StudyArea(bbox=(0, 0, 1, 1)),
        weights={"hydro": 35, "solar": 25, "elevation": 15},
        enabled_criteria=["hydro", "solar"],
    )
    ew = cfg.enabled_weights()
    assert abs(sum(ew.values()) - 100) < 0.01
    assert abs(ew["hydro"] - (35 / 60 * 100)) < 0.01


def test_default_config_weights():
    cfg = Config(study_area=StudyArea(bbox=(0, 0, 1, 1)))
    assert cfg.weights == dict(DEFAULT_WEIGHTS)
    assert sum(cfg.enabled_weights().values()) == pytest.approx(100.0, abs=0.01)

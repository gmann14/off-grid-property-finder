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


# --- wind settings ----------------------------------------------------------

def test_wind_defaults_are_30m_hub_from_gwa_10_and_50():
    from src.constants import WIND_GWA_LAYERS, WIND_HUB_HEIGHT_M

    cfg = Config(study_area=StudyArea(bbox=(0, 0, 1, 1)))
    assert cfg.wind.hub_height_m == WIND_HUB_HEIGHT_M == 30.0
    assert cfg.wind.gwa_layers == WIND_GWA_LAYERS
    assert sorted(cfg.wind.gwa_layers) == [10, 50]


def test_wind_settings_read_from_yaml(tmp_path):
    config_data = {
        "study_area": {"bbox": [1, 2, 3, 4]},
        "wind": {"hub_height_m": 24, "gwa_layers": {50: "https://x/50m.tif", 10: "https://x/10m.tif"}},
    }
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml.dump(config_data))
    cfg = load_config(config_file)
    assert cfg.wind.hub_height_m == 24.0
    assert list(cfg.wind.gwa_layers) == [10, 50]  # normalized to ascending height


def test_repo_config_yaml_sets_30m_hub():
    cfg = load_config("config.yaml")
    assert cfg.wind.hub_height_m == 30.0
    assert sorted(cfg.wind.gwa_layers) == [10, 50]
    assert all(url.endswith(f"_{h}m.tif") for h, url in cfg.wind.gwa_layers.items())


@pytest.mark.parametrize("wind", [
    {"hub_height_m": 0},
    {"hub_height_m": -30},
    {"gwa_layers": {10: "https://x/10m.tif"}},                       # one layer can't fit a shear
    {"gwa_layers": {10: "a", 50: "b", 100: "c"}},                     # ambiguous
    {"gwa_layers": {0: "https://x/0m.tif", 50: "https://x/50m.tif"}},  # non-positive height
    {"gwa_layers": {10: "", 50: "https://x/50m.tif"}},                # empty URL
])
def test_invalid_wind_settings_raise(tmp_path, wind):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml.dump({"study_area": {"bbox": [1, 2, 3, 4]}, "wind": wind}))
    with pytest.raises(ValueError):
        load_config(config_file)

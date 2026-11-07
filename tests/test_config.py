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

def _load(tmp_path, data):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml.dump(data))
    return load_config(config_file)


def test_wind_defaults_are_30m_hub_from_gwa_50_and_100():
    from src.constants import WIND_GWA_LAYERS, WIND_HUB_HEIGHT_M

    cfg = Config(study_area=StudyArea(bbox=(0, 0, 1, 1)))
    assert cfg.wind.hub_height_m == WIND_HUB_HEIGHT_M == 30.0
    assert cfg.wind.gwa_layers == WIND_GWA_LAYERS
    assert sorted(cfg.wind.gwa_layers) == [50, 100]


def test_config_without_wind_block_gets_the_defaults(tmp_path):
    # Configs from before the wind: block existed keep working unchanged.
    from src.config import WindSettings

    cfg = _load(tmp_path, {"study_area": {"bbox": [1, 2, 3, 4]}})
    assert cfg.wind == WindSettings()
    cfg = _load(tmp_path, {"study_area": {"bbox": [1, 2, 3, 4]}, "wind": None})  # bare "wind:"
    assert cfg.wind == WindSettings()


def test_wind_settings_read_from_yaml(tmp_path):
    cfg = _load(tmp_path, {
        "study_area": {"bbox": [1, 2, 3, 4]},
        "wind": {"hub_height_m": 24, "gwa_layers": {100: " https://x/100m.tif ", 50: "https://x/50m.tif"}},
    })
    assert cfg.wind.hub_height_m == 24.0
    assert list(cfg.wind.gwa_layers) == [50, 100]  # normalized to ascending height
    assert cfg.wind.gwa_layers[100] == "https://x/100m.tif"  # stripped


def test_repo_config_yaml_sets_30m_hub_from_50_and_100():
    cfg = load_config("config.yaml")
    assert cfg.wind.hub_height_m == 30.0
    assert sorted(cfg.wind.gwa_layers) == [50, 100]
    assert all(url.endswith(f"_{h}m.tif") for h, url in cfg.wind.gwa_layers.items())


@pytest.mark.parametrize("hub", [12, 20.5, 50, 100, 140])
def test_any_positive_hub_height_is_accepted(tmp_path, hub):
    from src.constants import WIND_GWA_LAYERS

    cfg = _load(tmp_path, {"study_area": {"bbox": [1, 2, 3, 4]},
                           "wind": {"hub_height_m": hub, "gwa_layers": dict(WIND_GWA_LAYERS)}})
    assert cfg.wind.hub_height_m == float(hub)


_LAYERS = {50: "https://x/50m.tif", 100: "https://x/100m.tif"}


@pytest.mark.parametrize("wind, message", [
    ({"hub_height_m": 0, "gwa_layers": _LAYERS}, "> 0"),
    ({"hub_height_m": -30, "gwa_layers": _LAYERS}, "> 0"),
    ({"hub_height_m": float("nan"), "gwa_layers": _LAYERS}, "finite"),
    ({"hub_height_m": float("inf"), "gwa_layers": _LAYERS}, "finite"),
    ({"hub_height_m": "thirty", "gwa_layers": _LAYERS}, "number of metres"),
    ({"hub_height_m": "30", "gwa_layers": _LAYERS}, "number of metres"),   # quoted number
    ({"hub_height_m": True, "gwa_layers": _LAYERS}, "number of metres"),
    ({"hub_height_m": None, "gwa_layers": _LAYERS}, "number of metres"),   # "hub_height_m:" left blank
    ({"hub_height_m": [30], "gwa_layers": _LAYERS}, "number of metres"),
    ({"hub_height_m": 30}, "missing key"),                                 # partial block
    ({"gwa_layers": _LAYERS}, "missing key"),
    ({"hub_height": 50, "gwa_layers": _LAYERS}, "unknown key"),            # typo
    ({"hub_height_m": 30, "gwa_layers": _LAYERS, "extra": 1}, "unknown key"),
    ("30", "mapping"),                                                     # wind: 30
    ({"hub_height_m": 30, "gwa_layers": {50: "https://x/50m.tif"}}, "exactly two"),
    ({"hub_height_m": 30, "gwa_layers": {10: "a", 50: "b", 100: "c"}}, "exactly two"),
    ({"hub_height_m": 30, "gwa_layers": {0: "https://x/0m.tif", 50: "https://x/50m.tif"}}, "> 0"),
    ({"hub_height_m": 30, "gwa_layers": {50.5: "a", 100: "b"}}, "whole metres"),
    ({"hub_height_m": 30, "gwa_layers": {50: "a", 50.0: "b"}}, "exactly two"),  # one key: 50 == 50.0
    ({"hub_height_m": 30, "gwa_layers": {"fifty": "a", 100: "b"}}, "number of metres"),
    ({"hub_height_m": 30, "gwa_layers": {50: "", 100: "https://x/100m.tif"}}, "non-empty URL"),
    ({"hub_height_m": 30, "gwa_layers": {50: None, 100: "https://x/100m.tif"}}, "non-empty URL"),
    ({"hub_height_m": 30, "gwa_layers": ["https://x/50m.tif", "https://x/100m.tif"]}, "must map"),
])
def test_invalid_wind_settings_raise_clearly(tmp_path, wind, message):
    with pytest.raises(ValueError, match=message):
        _load(tmp_path, {"study_area": {"bbox": [1, 2, 3, 4]}, "wind": wind})



def _write(tmp_path, **extra):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml.dump({"study_area": {"bbox": [1, 2, 3, 4]}, **extra}))
    return config_file


def test_min_parcel_area_defaults_to_one_acre_and_reads_from_yaml(tmp_path):
    assert load_config(_write(tmp_path)).min_parcel_area_acres == 1.0
    assert load_config(_write(tmp_path, min_parcel_area_acres=2.5)).min_parcel_area_acres == 2.5
    assert load_config(_write(tmp_path, min_parcel_area_acres=0)).min_parcel_area_acres == 0.0


@pytest.mark.parametrize("bad", [-1, "1", True, None, float("nan"), float("inf"), [1]])
def test_invalid_min_parcel_area_raises_clearly(tmp_path, bad):
    with pytest.raises(ValueError, match="min_parcel_area_acres"):
        load_config(_write(tmp_path, min_parcel_area_acres=bad))

"""Tests for config loading and validation."""

import pytest
import yaml

from src.config import Config, StudyArea, load_config
from src.constants import DEFAULT_WEIGHTS

BBOX = [380000, 4900000, 381000, 4901000]  # 1 km square, EPSG:2961 metres


def test_load_config_from_yaml(tmp_path):
    config_data = {
        "study_area": {"bbox": BBOX, "name": "test"},
        "cell_size_m": 500,
        "weights": {"hydro": 50, "solar": 50},
        "enabled_criteria": ["hydro", "solar"],
    }
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml.dump(config_data))

    cfg = load_config(config_file)
    assert cfg.study_area.bbox == tuple(BBOX)
    assert cfg.cell_size_m == 500
    assert cfg.weights == {"hydro": 50, "solar": 50}


def test_load_config_missing_file():
    with pytest.raises(FileNotFoundError):
        load_config("/nonexistent/config.yaml")


def test_enabled_criterion_without_weight(tmp_path):
    config_data = {
        "study_area": {"bbox": BBOX},
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

    cfg = _load(tmp_path, {"study_area": {"bbox": BBOX}})
    assert cfg.wind == WindSettings()
    cfg = _load(tmp_path, {"study_area": {"bbox": BBOX}, "wind": None})  # bare "wind:"
    assert cfg.wind == WindSettings()


def test_wind_settings_read_from_yaml(tmp_path):
    cfg = _load(tmp_path, {
        "study_area": {"bbox": BBOX},
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

    cfg = _load(tmp_path, {"study_area": {"bbox": BBOX},
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
        _load(tmp_path, {"study_area": {"bbox": BBOX}, "wind": wind})



def _write(tmp_path, **extra):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml.dump({"study_area": {"bbox": BBOX}, **extra}))
    return config_file


def test_min_parcel_area_defaults_to_one_acre_and_reads_from_yaml(tmp_path):
    assert load_config(_write(tmp_path)).min_parcel_area_acres == 1.0
    assert load_config(_write(tmp_path, min_parcel_area_acres=2.5)).min_parcel_area_acres == 2.5
    assert load_config(_write(tmp_path, min_parcel_area_acres=0)).min_parcel_area_acres == 0.0


@pytest.mark.parametrize("bad", [-1, "1", True, None, float("nan"), float("inf"), [1]])
def test_invalid_min_parcel_area_raises_clearly(tmp_path, bad):
    with pytest.raises(ValueError, match="min_parcel_area_acres"):
        load_config(_write(tmp_path, min_parcel_area_acres=bad))


# --- value ranges and types -------------------------------------------------

def test_repo_config_yaml_loads_with_validated_values():
    from src.constants import CONFIDENCE_DEDUCTIONS

    cfg = load_config("config.yaml")
    assert cfg.study_area.bbox == (360000, 4880000, 410000, 4930000)
    assert cfg.cell_size_m == 250
    assert cfg.working_crs == "EPSG:2961"
    assert cfg.exclusion_overlap_threshold == 0.5
    assert cfg.weights == {"hydro": 40, "access": 20, "open_ground": 15, "wind": 15, "elevation": 10}
    assert cfg.enabled_criteria == ["hydro", "access", "open_ground", "wind", "elevation"]
    assert cfg.confidence_deductions == CONFIDENCE_DEDUCTIONS
    assert cfg.preferences == {"parcel_aggregation": {"top_n": 3, "cell_weight": 0.65}}


def test_known_criteria_match_the_scoring_registry():
    # config.py keeps a fixed list (importing the scorers there is circular);
    # this catches a scorer added or renamed without updating it.
    import src.score  # noqa: F401  (registers every scorer)
    from src.config import KNOWN_CRITERIA
    from src.scoring.registry import available_scorers

    assert sorted(KNOWN_CRITERIA) == sorted(available_scorers())


@pytest.mark.parametrize("bad", [
    50,              # a percent, not a fraction: nothing would ever be excluded
    1.0001, 0, 0.0, -0.5,
    float("nan"), float("inf"),
    "0.5", True, None, [0.5],
])
def test_invalid_exclusion_overlap_threshold_raises_clearly(tmp_path, bad):
    with pytest.raises(ValueError, match=r"exclusion_overlap_threshold must be a fraction of the cell in \(0, 1\]"):
        load_config(_write(tmp_path, exclusion_overlap_threshold=bad))


@pytest.mark.parametrize("good", [1, 1.0, 0.5, 0.01, 1e-9])
def test_exclusion_overlap_threshold_edges_load(tmp_path, good):
    assert load_config(_write(tmp_path, exclusion_overlap_threshold=good)).exclusion_overlap_threshold == good


def test_exclusion_overlap_threshold_defaults_to_half(tmp_path):
    assert load_config(_write(tmp_path)).exclusion_overlap_threshold == 0.5


@pytest.mark.parametrize("bbox, message", [
    ([410000, 4930000, 360000, 4880000], "xmin < xmax and ymin < ymax"),   # reversed: 0 cells
    ([410000, 4880000, 360000, 4930000], "xmin < xmax and ymin < ymax"),   # x reversed
    ([360000, 4930000, 410000, 4880000], "xmin < xmax and ymin < ymax"),   # y reversed
    ([360000, 4880000, 360000, 4930000], "xmin < xmax and ymin < ymax"),   # zero width
    ([-64.6, 44.2, -64.0, 44.6], "looks like longitude/latitude degrees.*EPSG:2961"),  # 1 cell
    ([-180, -90, 180, 90], "metres"),
    ([1, 2, 3, 4], "metres"),
    ([360000, 4880000, 410000], "4 finite numbers"),
    ([360000, 4880000, 410000, 4930000, 0], "4 finite numbers"),
    ([360000, 4880000, 410000, float("nan")], "4 finite numbers"),
    ([360000, 4880000, float("inf"), 4930000], "4 finite numbers"),
    ([360000, 4880000, "410000", 4930000], "4 finite numbers"),
    ([360000, 4880000, True, 4930000], "4 finite numbers"),
    ([360000, 4880000, None, 4930000], "4 finite numbers"),
    ("360000 4880000 410000 4930000", "4 finite numbers"),
    (None, "4 finite numbers"),                                          # "bbox:" left blank
])
def test_invalid_bbox_raises_clearly(tmp_path, bbox, message):
    with pytest.raises(ValueError, match=f"study_area.bbox.*{message}"):
        load_config(_write(tmp_path, study_area={"bbox": bbox}))


@pytest.mark.parametrize("bbox", [
    [360000, 4880000, 410000, 4930000],
    [360000.5, 4880000.25, 360001, 4880001],      # tiny, fractional metres
    [-500000, -500000, 500000, 500000],           # straddles the origin
    [100, 200, 400000, 4900000],                  # some values small, not all
])
def test_metre_bboxes_load(tmp_path, bbox):
    assert load_config(_write(tmp_path, study_area={"bbox": bbox})).study_area.bbox == tuple(bbox)


def test_bbox_defaults_to_lunenburg(tmp_path):
    cfg = load_config(_write(tmp_path, study_area={"name": "x"}))
    assert cfg.study_area.bbox == (360000, 4880000, 410000, 4930000)


@pytest.mark.parametrize("crs, message", [
    ("EPSG:4326", "projected CRS in metres"),       # lon/lat degrees
    ("EPSG:4269", "projected CRS in metres"),       # NAD83 geographic
    ("EPSG:2263", "projected CRS in metres"),       # NY State Plane, US survey feet
    ("EPSG:4978", "projected CRS in metres"),       # geocentric
    ("EPSG:99999", "not a CRS pyproj recognises"),
    ("not a crs", "not a CRS pyproj recognises"),
    ("", "CRS string"),
    (2961, "CRS string"),
    (None, "CRS string"),
])
def test_invalid_working_crs_raises_clearly(tmp_path, crs, message):
    with pytest.raises(ValueError, match=f"working_crs.*{message}"):
        load_config(_write(tmp_path, working_crs=crs))


@pytest.mark.parametrize("crs", ["EPSG:2961", "EPSG:32620", "EPSG:3857", "epsg:2961"])
def test_projected_metre_crs_loads(tmp_path, crs):
    assert load_config(_write(tmp_path, working_crs=crs)).working_crs == crs


@pytest.mark.parametrize("bad", ["250", 0, -250, 250.0, 250.5, True, None, [250]])
def test_invalid_cell_size_raises_clearly(tmp_path, bad):
    with pytest.raises(ValueError, match="cell_size_m must be a whole number of metres > 0"):
        load_config(_write(tmp_path, cell_size_m=bad))


@pytest.mark.parametrize("good", [1, 30, 250, 1000])
def test_positive_whole_cell_sizes_load(tmp_path, good):
    cfg = load_config(_write(tmp_path, cell_size_m=good))
    assert cfg.cell_size_m == good and type(cfg.cell_size_m) is int


@pytest.mark.parametrize("weights, message", [
    ({**DEFAULT_WEIGHTS, "hydro": -40}, r"weights\.hydro must be a finite number >= 0"),
    ({**DEFAULT_WEIGHTS, "hydro": float("nan")}, r"weights\.hydro must be a finite number >= 0"),
    ({**DEFAULT_WEIGHTS, "hydro": float("inf")}, r"weights\.hydro must be a finite number >= 0"),
    ({**DEFAULT_WEIGHTS, "hydro": "forty"}, r"weights\.hydro must be a finite number >= 0"),
    ({**DEFAULT_WEIGHTS, "hydro": "40"}, r"weights\.hydro must be a finite number >= 0"),
    ({**DEFAULT_WEIGHTS, "hydro": True}, r"weights\.hydro must be a finite number >= 0"),
    ({**DEFAULT_WEIGHTS, "hydro": None}, r"weights\.hydro must be a finite number >= 0"),
    ({k: 0 for k in DEFAULT_WEIGHTS}, "weights sum to 0"),
    ([40, 20, 15, 15, 10], "weights: must be a mapping"),
])
def test_invalid_weights_raise_clearly(tmp_path, weights, message):
    with pytest.raises(ValueError, match=message):
        load_config(_write(tmp_path, weights=weights))


def test_enabled_weights_summing_to_zero_raise_even_if_disabled_ones_are_set(tmp_path):
    with pytest.raises(ValueError, match="weights sum to 0"):
        load_config(_write(tmp_path, weights={"hydro": 0, "access": 0, "solar": 50},
                           enabled_criteria=["hydro", "access"]))


@pytest.mark.parametrize("enabled, message", [
    (["hydro", "hydrro"], "unknown criterion 'hydrro'"),    # typo, no weight either
    (["hydro", "Hydro"], "unknown criterion 'Hydro'"),
    ("hydro", "non-empty list of criterion names"),         # a string, not a list
    ([], "non-empty list of criterion names"),
    (["hydro", 5], "non-empty list of criterion names"),
    (None, "non-empty list of criterion names"),            # "enabled_criteria:" left blank
])
def test_invalid_enabled_criteria_raise_clearly(tmp_path, enabled, message):
    with pytest.raises(ValueError, match=message):
        load_config(_write(tmp_path, enabled_criteria=enabled))


def test_unknown_criterion_with_a_weight_fails_at_load(tmp_path):
    # It used to load, renormalize, and only fail inside score (no such scorer).
    with pytest.raises(ValueError, match="unknown.*'hydrro'"):
        load_config(_write(tmp_path, weights={**DEFAULT_WEIGHTS, "hydrro": 10},
                           enabled_criteria=[*DEFAULT_WEIGHTS, "hydrro"]))


def test_unknown_criterion_error_lists_the_known_ones(tmp_path):
    with pytest.raises(ValueError) as e:
        load_config(_write(tmp_path, enabled_criteria=["hydrro"]))
    for name in ("hydro", "access", "open_ground", "wind", "elevation", "solar", "buildable"):
        assert repr(name) in str(e.value)


def test_weight_edges_load(tmp_path):
    # 0 for a disabled criterion, 0 for one of several enabled, floats, and the
    # registered-but-off solar/buildable criteria.
    cfg = load_config(_write(tmp_path,
                             weights={"hydro": 0.0, "access": 20.5, "open_ground": 0, "solar": 0,
                                      "buildable": 5},
                             enabled_criteria=["hydro", "access", "buildable"]))
    assert cfg.enabled_weights() == pytest.approx({"hydro": 0.0, "access": 20.5 / 25.5 * 100,
                                                   "buildable": 5 / 25.5 * 100})


def test_enabled_criteria_default_to_every_weighted_criterion(tmp_path):
    cfg = load_config(_write(tmp_path, weights={"hydro": 1, "solar": 3}))
    assert cfg.enabled_criteria == ["hydro", "solar"]


@pytest.mark.parametrize("agg, message", [
    ({"cell_weight": 1.5}, r"cell_weight must be a number in \[0, 1\]"),   # parcel score -16
    ({"cell_weight": -0.1}, r"cell_weight must be a number in \[0, 1\]"),
    ({"cell_weight": float("nan")}, r"cell_weight must be a number in \[0, 1\]"),
    ({"cell_weight": float("inf")}, r"cell_weight must be a number in \[0, 1\]"),
    ({"cell_weight": "0.65"}, r"cell_weight must be a number in \[0, 1\]"),
    ({"cell_weight": True}, r"cell_weight must be a number in \[0, 1\]"),
    ({"cell_weight": None}, r"cell_weight must be a number in \[0, 1\]"),
    ({"top_n": 0}, "top_n must be a whole number >= 1"),                    # ranks nothing
    ({"top_n": -3}, "top_n must be a whole number >= 1"),
    ({"top_n": 2.5}, "top_n must be a whole number >= 1"),
    ({"top_n": 3.0}, "top_n must be a whole number >= 1"),
    ({"top_n": "3"}, "top_n must be a whole number >= 1"),
    ({"top_n": True}, "top_n must be a whole number >= 1"),
    ({"top_n": None}, "top_n must be a whole number >= 1"),
])
def test_invalid_parcel_aggregation_raises_clearly(tmp_path, agg, message):
    with pytest.raises(ValueError, match=rf"preferences\.parcel_aggregation\.{message}"):
        load_config(_write(tmp_path, preferences={"parcel_aggregation": agg}))


@pytest.mark.parametrize("agg, top_n, cell_weight", [
    ({"top_n": 1, "cell_weight": 0}, 1, 0.0),
    ({"top_n": 10, "cell_weight": 1}, 10, 1.0),
    ({"top_n": 5, "cell_weight": 0.0}, 5, 0.0),
    ({"cell_weight": 1.0}, 3, 1.0),            # partial block: top_n defaults
    ({"top_n": 2}, 2, 0.65),                   # partial block: cell_weight defaults
    ({}, 3, 0.65),
])
def test_parcel_aggregation_edges_load_as_validated_values(tmp_path, agg, top_n, cell_weight):
    from src.constants import PARCEL_CELL_WEIGHT, PARCEL_TOP_N_CELLS

    cfg = load_config(_write(tmp_path, preferences={"parcel_aggregation": agg}))
    # read exactly as src/score.py does
    got = cfg.preferences.get("parcel_aggregation", {})
    assert got.get("top_n", PARCEL_TOP_N_CELLS) == top_n
    assert got.get("cell_weight", PARCEL_CELL_WEIGHT) == cell_weight
    assert type(got["top_n"]) is int and type(got["cell_weight"]) is float


def test_no_preferences_block_gives_the_default_parcel_aggregation(tmp_path):
    cfg = load_config(_write(tmp_path))
    assert cfg.preferences == {"parcel_aggregation": {"top_n": 3, "cell_weight": 0.65}}


# --- unknown keys, null blocks, empty files ---------------------------------

def _write_raw(tmp_path, text):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(text)
    return config_file


@pytest.mark.parametrize("extra, where, typo", [
    ({"weight": {"hydro": 100}}, "config.yaml", "weight"),          # silently used the defaults
    ({"min_parcel_area": 40}, "config.yaml", "min_parcel_area"),    # silently stayed at 1 acre
    ({"cellsize_m": 100}, "config.yaml", "cellsize_m"),
    ({"preference": {"parcel_aggregation": {"top_n": 5}}}, "config.yaml", "preference"),
    ({"study_area": {"bbox": BBOX, "nmae": "x"}}, "study_area", "nmae"),
    ({"study_area": {"box": BBOX}}, "study_area", "box"),
    ({"paths": {"proccessed": "elsewhere"}}, "paths", "proccessed"),
    ({"weights": {**DEFAULT_WEIGHTS, "hydrro": 10}}, "weights", "hydrro"),   # not enabled, still a typo
    ({"weights": {**DEFAULT_WEIGHTS, "solar_pv": 10}}, "weights", "solar_pv"),
    ({"preferences": {"parcel_agg": {"top_n": 5}}}, "preferences", "parcel_agg"),
    ({"preferences": {"parcel_aggregation": {"topn": 5}}}, "preferences.parcel_aggregation", "topn"),
    ({"preferences": {"parcel_aggregation": {"cell_weight": 0.5, "size_weight": 0.5}}},
     "preferences.parcel_aggregation", "size_weight"),
])
def test_unknown_keys_raise_and_list_the_allowed_ones(tmp_path, extra, where, typo):
    with pytest.raises(ValueError, match=rf"{where}: unknown key\(s\) \['{typo}'\]; allowed keys are \["):
        load_config(_write(tmp_path, **extra))


@pytest.mark.parametrize("where, allowed", [
    ("config.yaml", ["cell_size_m", "confidence_deductions", "enabled_criteria",
                     "exclusion_overlap_threshold", "min_parcel_area_acres", "paths",
                     "preferences", "study_area", "weights", "wind", "working_crs"]),
    ("study_area", ["bbox", "name"]),
    ("paths", ["output", "processed", "raw"]),
    ("weights", ["access", "buildable", "elevation", "hydro", "open_ground", "solar", "wind"]),
    ("preferences", ["parcel_aggregation"]),
    ("preferences.parcel_aggregation", ["cell_weight", "top_n"]),
])
def test_unknown_key_message_lists_exactly_the_allowed_keys(tmp_path, where, allowed):
    data = {"study_area": {"bbox": BBOX}}
    *parents, leaf = where.split(".")
    if where == "config.yaml":
        data["oops"] = 1
    else:
        block = data
        for key in parents:
            block = block.setdefault(key, {})
        block.setdefault(leaf, {})["oops"] = 1
    with pytest.raises(ValueError) as e:
        load_config(_write_raw(tmp_path, yaml.dump(data)))
    assert str(e.value).endswith(f"allowed keys are {allowed}")


@pytest.mark.parametrize("text, message", [
    ("study_area:\n", "study_area: is empty; it should hold bbox:"),
    ("study_area: null\n", "study_area: is empty"),
    ("study_area: [1, 2, 3, 4]\n", "study_area: must be a mapping"),
    ("paths:\n", "paths: is empty; it should hold raw:, processed: and/or output:"),
    ("paths: data\n", "paths: must be a mapping"),
    ("weights:\n", "weights: is empty; it should hold a weight >= 0 per criterion"),
    ("weights: {}\n", "weights: is empty"),
    ("preferences:\n  parcel_aggregation:\n", "preferences.parcel_aggregation: is empty; it should hold top_n"),
    ("preferences:\n  parcel_aggregation: 3\n", "preferences.parcel_aggregation: must be a mapping"),
    ("preferences: top_n\n", "preferences: must be a mapping"),
    ("enabled_criteria:\n", "enabled_criteria must be a non-empty list"),
])
def test_null_or_non_mapping_blocks_raise_saying_what_they_hold(tmp_path, text, message):
    bbox = "" if text.startswith("study_area") else f"study_area:\n  bbox: {BBOX}\n"
    with pytest.raises(ValueError, match=message):
        load_config(_write_raw(tmp_path, bbox + text))


@pytest.mark.parametrize("text", ["preferences:\n", "preferences: {}\n", "preferences: null\n"])
def test_empty_preferences_block_means_the_defaults(tmp_path, text):
    # it used to load as None and crash inside score after scored_cells.gpkg was written
    cfg = load_config(_write_raw(tmp_path, f"study_area:\n  bbox: {BBOX}\n{text}"))
    assert cfg.preferences == {"parcel_aggregation": {"top_n": 3, "cell_weight": 0.65}}


@pytest.mark.parametrize("text", ["", "\n", "# just a comment\n", "---\n"])
def test_empty_config_file_raises_clearly(tmp_path, text):
    with pytest.raises(ValueError, match=r"is empty \(or only comments\); it should be a mapping of config keys"):
        load_config(_write_raw(tmp_path, text))


@pytest.mark.parametrize("text", ["- study_area\n- weights\n", "42\n", "study_area\n"])
def test_non_mapping_config_file_raises_clearly(tmp_path, text):
    with pytest.raises(ValueError, match="must be a mapping of config keys"):
        load_config(_write_raw(tmp_path, text))


@pytest.mark.parametrize("block, key", [
    ("paths", "raw"), ("paths", "processed"), ("paths", "output"), ("study_area", "name"),
])
@pytest.mark.parametrize("bad", [None, "", "   ", 5])
def test_blank_or_non_string_paths_and_name_raise_clearly(tmp_path, block, key, bad):
    data = {"study_area": {"bbox": BBOX}}
    data.setdefault(block, {})[key] = bad
    with pytest.raises(ValueError, match=f"{block}.{key} must be a non-empty string"):
        load_config(_write_raw(tmp_path, yaml.dump(data)))


def test_absent_blocks_still_mean_the_defaults(tmp_path):
    from src.config import Paths

    cfg = load_config(_write_raw(tmp_path, "cell_size_m: 100\n"))
    assert cfg.study_area == StudyArea(bbox=(360000, 4880000, 410000, 4930000), name="lunenburg")
    assert cfg.paths == Paths()
    assert cfg.weights == DEFAULT_WEIGHTS and cfg.enabled_criteria == list(DEFAULT_WEIGHTS)


def test_partial_blocks_keep_the_other_defaults(tmp_path):
    from pathlib import Path

    cfg = load_config(_write(tmp_path, study_area={"bbox": BBOX}, paths={"output": "out2"}))
    assert cfg.study_area.name == "lunenburg"
    assert cfg.paths.raw == Path("data/raw") and cfg.paths.output == Path("out2")

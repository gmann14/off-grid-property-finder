"""Load and validate config.yaml."""

import math
import numbers
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pyproj import CRS
from pyproj.exceptions import CRSError

from src.constants import (
    CONFIDENCE_DEDUCTIONS,
    DEFAULT_CELL_SIZE_M,
    DEFAULT_EXCLUSION_OVERLAP_THRESHOLD,
    DEFAULT_WEIGHTS,
    PARCEL_CELL_WEIGHT,
    PARCEL_TOP_N_CELLS,
    WIND_GWA_LAYERS,
    WIND_HUB_HEIGHT_M,
    WORKING_CRS,
)

DEFAULT_BBOX = (360000, 4880000, 410000, 4930000)  # Lunenburg region, EPSG:2961

# Every name a scorer is registered under (``@register`` in src/scoring/*.py).
# A fixed list because src.scoring.registry imports this module, so importing
# the scorers here would be circular; tests/test_config.py checks it against
# the registry. solar and buildable are registered but off by default.
KNOWN_CRITERIA = (*DEFAULT_WEIGHTS, "solar", "buildable")


@dataclass
class StudyArea:
    bbox: tuple[float, float, float, float]  # (xmin, ymin, xmax, ymax) in working CRS
    name: str = "lunenburg"


@dataclass
class Paths:
    raw: Path = field(default_factory=lambda: Path("data/raw"))
    processed: Path = field(default_factory=lambda: Path("data/processed"))
    output: Path = field(default_factory=lambda: Path("output"))


@dataclass
class WindSettings:
    """Where the wind resource comes from and at what height it is scored.

    ``gwa_layers`` maps a GWA layer height (m) to its COG URL; exactly two are
    used to fit a per-pixel power-law shear profile, which is evaluated at
    ``hub_height_m``. Changing either invalidates processed/wind.tif (tracked in
    the processed-data manifest, see src/manifest.py).
    """

    hub_height_m: float = WIND_HUB_HEIGHT_M
    gwa_layers: dict[int, str] = field(default_factory=lambda: dict(WIND_GWA_LAYERS))

    def __post_init__(self) -> None:
        self.hub_height_m = _positive_metres(self.hub_height_m, "wind.hub_height_m")
        if not isinstance(self.gwa_layers, dict):
            raise ValueError(
                f"wind.gwa_layers must map layer height (m) to a GWA COG URL, got {self.gwa_layers!r}"
            )
        layers: dict[int, str] = {}
        for height, url in self.gwa_layers.items():
            h = _positive_metres(height, f"wind.gwa_layers key {height!r}")
            if h != int(h):
                raise ValueError(f"wind.gwa_layers heights must be whole metres, got {height!r}")
            if not isinstance(url, str) or not url.strip():
                raise ValueError(f"wind.gwa_layers[{height!r}] must be a non-empty URL, got {url!r}")
            layers[int(h)] = url.strip()
        if len(layers) != 2:
            raise ValueError(
                f"wind.gwa_layers needs exactly two heights to fit a shear profile, got {sorted(layers)}"
            )
        self.gwa_layers = dict(sorted(layers.items()))


def _positive_metres(value, name: str) -> float:
    """``value`` as a finite float > 0, or a ValueError naming ``name``.
    Only real numbers count: a bool (``true``) or a quoted string (``"30"``)
    in config.yaml is almost always a typo, so it fails instead of coercing."""
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise ValueError(f"{name} must be a number of metres, got {value!r}")
    f = float(value)
    if not math.isfinite(f) or f <= 0:
        raise ValueError(f"{name} must be a finite number > 0, got {value!r}")
    return f


_WIND_KEYS = {"hub_height_m", "gwa_layers"}


def _load_wind(raw) -> WindSettings:
    """No ``wind:`` block (as in configs from before it existed) -> the
    defaults. A block that is present must give both keys, so a typo such as
    ``hub_height: 50`` fails loudly instead of silently scoring at 30 m."""
    if raw is None:
        return WindSettings()
    if not isinstance(raw, dict):
        raise ValueError(f"wind: must be a mapping with {sorted(_WIND_KEYS)}, got {raw!r}")
    unknown = set(raw) - _WIND_KEYS
    if unknown:
        raise ValueError(f"wind: unknown key(s) {sorted(unknown)}; expected {sorted(_WIND_KEYS)}")
    missing = _WIND_KEYS - set(raw)
    if missing:
        raise ValueError(f"wind: missing key(s) {sorted(missing)}; give both {sorted(_WIND_KEYS)} "
                         "or drop the wind: block to use the defaults")
    return WindSettings(hub_height_m=raw["hub_height_m"], gwa_layers=raw["gwa_layers"])


@dataclass
class Config:
    study_area: StudyArea
    cell_size_m: int = DEFAULT_CELL_SIZE_M
    working_crs: str = WORKING_CRS
    paths: Paths = field(default_factory=Paths)
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    enabled_criteria: list[str] = field(
        default_factory=lambda: list(DEFAULT_WEIGHTS.keys())
    )
    confidence_deductions: dict[str, int] = field(
        default_factory=lambda: dict(CONFIDENCE_DEDUCTIONS)
    )
    exclusion_overlap_threshold: float = 0.5
    min_parcel_area_acres: float = 1.0
    preferences: dict[str, dict] = field(default_factory=dict)
    wind: WindSettings = field(default_factory=WindSettings)

    def enabled_weights(self) -> dict[str, float]:
        raw = {k: v for k, v in self.weights.items() if k in self.enabled_criteria}
        total = sum(raw.values())
        if total == 0:
            return raw
        return {k: v / total * 100 for k, v in raw.items()}


def _min_parcel_area(value: object) -> float:
    """``min_parcel_area_acres``: a finite number >= 0 (0 turns the minimum off)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f"min_parcel_area_acres must be a number >= 0 (acres of land), got {value!r}")
    return float(value)


def _finite_number(value: object) -> bool:
    """A finite real number that isn't a bool: ``true`` or a quoted ``"0.5"``
    in config.yaml is almost always a typo, so it fails instead of coercing."""
    return not isinstance(value, bool) and isinstance(value, numbers.Real) and math.isfinite(value)


def _whole_number(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, numbers.Integral)


def _working_crs(value: object) -> str:
    """``working_crs``: a projected CRS in metres. The grid, buffers and acreages
    are all computed in its units, so degrees (EPSG:4326) or feet would silently
    give a nonsense grid."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"working_crs must be a CRS string such as 'EPSG:2961', got {value!r}")
    try:
        crs = CRS.from_user_input(value)
    except CRSError as e:
        raise ValueError(f"working_crs {value!r} is not a CRS pyproj recognises: {e}") from e
    units = sorted({axis.unit_name for axis in crs.axis_info})
    if not crs.is_projected or units not in (["metre"], ["meter"]):
        raise ValueError(
            f"working_crs must be a projected CRS in metres, got {value!r} "
            f"({crs.name}; axis units {units})"
        )
    return value


def _bbox(value: object, working_crs: str) -> tuple:
    """``study_area.bbox``: [xmin, ymin, xmax, ymax] in the working CRS's metres.
    A reversed box gives an empty grid and a lon/lat box a one-cell grid, both
    without an error, so both fail here. Values are kept as written (the
    processed-data manifest records them)."""
    if (not isinstance(value, (list, tuple)) or len(value) != 4
            or not all(_finite_number(v) for v in value)):
        raise ValueError(
            f"study_area.bbox must be 4 finite numbers [xmin, ymin, xmax, ymax], got {value!r}"
        )
    xmin, ymin, xmax, ymax = value
    if not (xmin < xmax and ymin < ymax):
        raise ValueError(
            f"study_area.bbox must be [xmin, ymin, xmax, ymax] with xmin < xmax and ymin < ymax, "
            f"got {list(value)}"
        )
    # working_crs is projected by now (_working_crs), so a box this small is
    # almost certainly lon/lat degrees, not a 360 m x 360 m study area.
    if all(abs(v) <= 180 for v in value):
        raise ValueError(
            f"study_area.bbox {list(value)} looks like longitude/latitude degrees; it must be "
            f"in the working CRS's metres ({working_crs}), e.g. {list(DEFAULT_BBOX)}"
        )
    return tuple(value)


def _cell_size(value: object) -> int:
    """``cell_size_m``: a whole number of metres > 0."""
    if not _whole_number(value) or value <= 0:
        raise ValueError(f"cell_size_m must be a whole number of metres > 0, got {value!r}")
    return int(value)


def _overlap_threshold(value: object) -> float:
    """``exclusion_overlap_threshold``: the fraction of a cell an exclusion must
    cover, in (0, 1]. A percent (50) would never be reached, so nothing would
    ever be excluded."""
    if not _finite_number(value) or not 0 < value <= 1:
        raise ValueError(
            "exclusion_overlap_threshold must be a fraction of the cell in (0, 1] "
            f"(0.5 = half the cell, not 50), got {value!r}"
        )
    return float(value)


def _weights(weights: object) -> dict[str, float]:
    """``weights``: each a finite number >= 0. A negative or NaN weight gives
    composite scores outside 0-100, or all NaN."""
    if not isinstance(weights, dict):
        raise ValueError(
            f"weights must map criterion name to a weight >= 0 (known criteria: "
            f"{list(KNOWN_CRITERIA)}), got {weights!r}"
        )
    for name, weight in weights.items():
        if not _finite_number(weight) or weight < 0:
            raise ValueError(f"weights.{name} must be a finite number >= 0, got {weight!r}")
    return dict(weights)


def _enabled_criteria(enabled: object, weights: dict[str, float]) -> list[str]:
    """``enabled_criteria``: known criterion names, each with a weight, whose
    weights sum to > 0 (an all-zero set scores every cell 0)."""
    if not isinstance(enabled, list) or not enabled or not all(isinstance(c, str) for c in enabled):
        raise ValueError(
            f"enabled_criteria must be a non-empty list of criterion names (known criteria: "
            f"{list(KNOWN_CRITERIA)}), got {enabled!r}"
        )
    for criterion in enabled:
        if criterion not in KNOWN_CRITERIA:
            raise ValueError(
                f"enabled_criteria: unknown criterion {criterion!r}; known criteria are "
                f"{list(KNOWN_CRITERIA)}"
            )
        if criterion not in weights:
            raise ValueError(
                f"Enabled criterion '{criterion}' has no weight defined"
            )
    enabled_weights = {c: weights[c] for c in enabled}
    if sum(enabled_weights.values()) <= 0:
        raise ValueError(
            f"the enabled criteria's weights sum to 0 ({enabled_weights}); "
            "give at least one enabled criterion a weight > 0"
        )
    return list(enabled)


def _load_preferences(raw: dict) -> dict:
    """``preferences:`` with ``parcel_aggregation`` range-checked and its
    defaults filled in, so src/score.py's ``.get(...)`` reads validated values.
    ``cell_weight`` outside [0, 1] gives negative parcel scores; ``top_n`` < 1
    ranks nothing."""
    prefs = dict(raw)
    agg = dict(prefs.get("parcel_aggregation", {}))
    top_n = agg.get("top_n", PARCEL_TOP_N_CELLS)
    if not _whole_number(top_n) or top_n < 1:
        raise ValueError(
            "preferences.parcel_aggregation.top_n must be a whole number >= 1 "
            f"(cells averaged per parcel), got {top_n!r}"
        )
    cell_weight = agg.get("cell_weight", PARCEL_CELL_WEIGHT)
    if not _finite_number(cell_weight) or not 0 <= cell_weight <= 1:
        raise ValueError(
            "preferences.parcel_aggregation.cell_weight must be a number in [0, 1] "
            f"(the cell-score share of the parcel score; the rest is acreage), got {cell_weight!r}"
        )
    agg["top_n"] = int(top_n)
    agg["cell_weight"] = float(cell_weight)
    prefs["parcel_aggregation"] = agg
    return prefs


def load_config(path: str | Path = "config.yaml") -> Config:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path) as f:
        raw = yaml.safe_load(f)

    working_crs = _working_crs(raw.get("working_crs", WORKING_CRS))

    study_area_raw = raw.get("study_area", {})
    study_area = StudyArea(
        bbox=_bbox(study_area_raw.get("bbox", list(DEFAULT_BBOX)), working_crs),
        name=study_area_raw.get("name", "lunenburg"),
    )

    paths_raw = raw.get("paths", {})
    paths = Paths(
        raw=Path(paths_raw.get("raw", "data/raw")),
        processed=Path(paths_raw.get("processed", "data/processed")),
        output=Path(paths_raw.get("output", "output")),
    )

    weights = _weights(raw.get("weights", dict(DEFAULT_WEIGHTS)))
    enabled = _enabled_criteria(raw.get("enabled_criteria", list(weights)), weights)

    wind = _load_wind(raw.get("wind"))

    return Config(
        study_area=study_area,
        cell_size_m=_cell_size(raw.get("cell_size_m", DEFAULT_CELL_SIZE_M)),
        working_crs=working_crs,
        paths=paths,
        weights=weights,
        enabled_criteria=enabled,
        confidence_deductions=raw.get(
            "confidence_deductions", dict(CONFIDENCE_DEDUCTIONS)
        ),
        exclusion_overlap_threshold=_overlap_threshold(
            raw.get("exclusion_overlap_threshold", DEFAULT_EXCLUSION_OVERLAP_THRESHOLD)
        ),
        min_parcel_area_acres=_min_parcel_area(raw.get("min_parcel_area_acres", 1.0)),
        preferences=_load_preferences(raw.get("preferences", {})),
        wind=wind,
    )

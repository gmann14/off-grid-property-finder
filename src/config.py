"""Load and validate config.yaml."""

import math
import numbers
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from src.constants import (
    CONFIDENCE_DEDUCTIONS,
    DEFAULT_CELL_SIZE_M,
    DEFAULT_WEIGHTS,
    WIND_GWA_LAYERS,
    WIND_HUB_HEIGHT_M,
    WORKING_CRS,
)


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


def load_config(path: str | Path = "config.yaml") -> Config:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path) as f:
        raw = yaml.safe_load(f)

    study_area_raw = raw.get("study_area", {})
    study_area = StudyArea(
        bbox=tuple(study_area_raw.get("bbox", [360000, 4880000, 410000, 4930000])),
        name=study_area_raw.get("name", "lunenburg"),
    )

    paths_raw = raw.get("paths", {})
    paths = Paths(
        raw=Path(paths_raw.get("raw", "data/raw")),
        processed=Path(paths_raw.get("processed", "data/processed")),
        output=Path(paths_raw.get("output", "output")),
    )

    weights = raw.get("weights", dict(DEFAULT_WEIGHTS))
    enabled = raw.get("enabled_criteria", list(weights.keys()))

    for criterion in enabled:
        if criterion not in weights:
            raise ValueError(
                f"Enabled criterion '{criterion}' has no weight defined"
            )

    wind = _load_wind(raw.get("wind"))

    return Config(
        study_area=study_area,
        cell_size_m=raw.get("cell_size_m", DEFAULT_CELL_SIZE_M),
        working_crs=raw.get("working_crs", WORKING_CRS),
        paths=paths,
        weights=weights,
        enabled_criteria=enabled,
        confidence_deductions=raw.get(
            "confidence_deductions", dict(CONFIDENCE_DEDUCTIONS)
        ),
        exclusion_overlap_threshold=raw.get("exclusion_overlap_threshold", 0.5),
        min_parcel_area_acres=raw.get("min_parcel_area_acres", 1.0),
        preferences=raw.get("preferences", {}),
        wind=wind,
    )

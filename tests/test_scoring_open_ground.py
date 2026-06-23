"""Tests for open-ground capacity scoring (merged solar + buildable)."""

import numpy as np
import pandas as pd
import rasterio
from rasterio.transform import from_bounds

from src.config import Config, Paths, StudyArea
from src.constants import OPEN_GROUND_PERCENT_THRESHOLDS
from src.scoring.open_ground import _lookup_score, score_open_ground

CRS = "EPSG:2961"
BBOX = (380000.0, 4900000.0, 381000.0, 4901000.0)


def _write_mask(path, fraction_buildable):
    """Write a 0/1 buildability mask where `fraction_buildable` of pixels = 1."""
    w = h = 100
    transform = from_bounds(*BBOX, w, h)
    arr = np.zeros((h, w), dtype=np.uint8)
    n = int(fraction_buildable * w * h)
    arr.flat[:n] = 1
    with rasterio.open(path, "w", driver="GTiff", height=h, width=w, count=1,
                       dtype="uint8", crs=CRS, transform=transform, nodata=255) as d:
        d.write(arr, 1)


def _cfg(tmp_path):
    return Config(study_area=StudyArea(bbox=BBOX, name="t"),
                  paths=Paths(raw=tmp_path/"raw", processed=tmp_path, output=tmp_path/"o"))


def test_lookup_thresholds():
    assert _lookup_score(50, OPEN_GROUND_PERCENT_THRESHOLDS) == 100
    assert _lookup_score(25, OPEN_GROUND_PERCENT_THRESHOLDS) == 85
    assert _lookup_score(1, OPEN_GROUND_PERCENT_THRESHOLDS) == 0


def test_open_ground_discriminates_by_area(tmp_path, small_grid):
    # A mostly-open mask should score high; a mostly-constrained one low.
    _write_mask(tmp_path / "buildability_mask.tif", fraction_buildable=0.9)
    scores_high = score_open_ground(small_grid, _cfg(tmp_path))
    assert (scores_high == 100).all()


def test_open_ground_low_when_constrained(tmp_path, small_grid):
    _write_mask(tmp_path / "buildability_mask.tif", fraction_buildable=0.01)
    scores = score_open_ground(small_grid, _cfg(tmp_path))
    # ~1% open ground → bottom threshold
    assert (scores <= 20).all()


def test_open_ground_no_mask(tmp_path, small_grid):
    scores = score_open_ground(small_grid, _cfg(tmp_path))
    assert (scores == 0).all()

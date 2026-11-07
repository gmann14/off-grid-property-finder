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


# --- south bonus: share of the OPEN ground that faces south -------------------

def _write(path, arr, dtype, nodata):
    h, w = arr.shape
    with rasterio.open(path, "w", driver="GTiff", height=h, width=w, count=1, dtype=dtype,
                       crs=CRS, transform=from_bounds(*BBOX, w, h), nodata=nodata) as d:
        d.write(arr.astype(dtype), 1)


def _open_rows_mask():
    """20% open ground in every 250 m cell (every 5th 10 m row) -> base 85."""
    mask = np.zeros((100, 100), dtype=np.uint8)
    mask[::5, :] = 1
    return mask


def test_south_bonus_needs_the_open_ground_itself_to_face_south(tmp_path, small_grid):
    """Regression: the bonus was earned when 25% of the WHOLE cell faced
    90-270 deg, whatever faced south. Here the open rows face north and the
    forest between them faces south (80% of the cell)."""
    mask = _open_rows_mask()
    _write(tmp_path / "buildability_mask.tif", mask, "uint8", 255)
    _write(tmp_path / "aspect.tif", np.where(mask == 1, 0.0, 180.0), "float32", -9999)
    assert (score_open_ground(small_grid, _cfg(tmp_path)) == 85).all()


def test_south_bonus_counts_south_facing_open_ground(tmp_path, small_grid):
    """The open rows face south (only 20% of the cell, which missed the old
    whole-cell 25% cut): all of the open ground faces south, so +10."""
    mask = _open_rows_mask()
    _write(tmp_path / "buildability_mask.tif", mask, "uint8", 255)
    _write(tmp_path / "aspect.tif", np.where(mask == 1, 180.0, 0.0), "float32", -9999)
    assert (score_open_ground(small_grid, _cfg(tmp_path)) == 95).all()


def test_no_south_bonus_without_open_ground(tmp_path, small_grid):
    """Regression: 10,182 real cells with 0% open ground scored 10, not 0."""
    _write(tmp_path / "buildability_mask.tif", np.zeros((100, 100)), "uint8", 255)
    _write(tmp_path / "aspect.tif", np.full((100, 100), 180.0), "float32", -9999)
    assert (score_open_ground(small_grid, _cfg(tmp_path)) == 0).all()

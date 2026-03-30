"""Tests for buildable area scoring."""

import pytest

from src.scoring.buildable import _lookup_score
from src.constants import BUILDABLE_PERCENT_THRESHOLDS


def test_high_buildable():
    assert _lookup_score(50, BUILDABLE_PERCENT_THRESHOLDS) == 100


def test_medium_buildable():
    assert _lookup_score(25, BUILDABLE_PERCENT_THRESHOLDS) == 80


def test_low_buildable():
    assert _lookup_score(12, BUILDABLE_PERCENT_THRESHOLDS) == 60


def test_very_low_buildable():
    assert _lookup_score(7, BUILDABLE_PERCENT_THRESHOLDS) == 30


def test_zero_buildable():
    assert _lookup_score(2, BUILDABLE_PERCENT_THRESHOLDS) == 0

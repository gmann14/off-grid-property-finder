"""Tests for buildable area scoring."""

import pytest

from src.scoring.bands import band_score
from src.constants import BUILDABLE_PERCENT_THRESHOLDS


def test_high_buildable():
    assert band_score(50, BUILDABLE_PERCENT_THRESHOLDS) == 100


def test_medium_buildable():
    assert band_score(25, BUILDABLE_PERCENT_THRESHOLDS) == 80


def test_low_buildable():
    assert band_score(12, BUILDABLE_PERCENT_THRESHOLDS) == 60


def test_very_low_buildable():
    assert band_score(7, BUILDABLE_PERCENT_THRESHOLDS) == 30


def test_zero_buildable():
    assert band_score(2, BUILDABLE_PERCENT_THRESHOLDS) == 0

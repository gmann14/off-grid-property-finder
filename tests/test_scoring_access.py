"""Tests for access scoring."""

import pytest

from src.scoring.access import _lookup_score
from src.constants import ACCESS_DISTANCE_THRESHOLDS


def test_distance_zero_max_score():
    assert _lookup_score(0, ACCESS_DISTANCE_THRESHOLDS) == 100


def test_distance_25m():
    assert _lookup_score(25, ACCESS_DISTANCE_THRESHOLDS) == 80


def test_distance_100m():
    assert _lookup_score(100, ACCESS_DISTANCE_THRESHOLDS) == 50


def test_distance_300m():
    assert _lookup_score(300, ACCESS_DISTANCE_THRESHOLDS) == 20


def test_distance_1000m():
    assert _lookup_score(1000, ACCESS_DISTANCE_THRESHOLDS) == 0

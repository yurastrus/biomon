"""
Tests for helper functions in the pam_evaluation_utils module.

Covers lightweight functions without a DB:
  - convert_numpy_types
  - find_optimal_threshold
"""
import numpy as np
import pytest

from app.pam.pam_evaluation_utils import (
    convert_numpy_types,
    find_optimal_threshold,
)


def test_convert_numpy_int():
    val = convert_numpy_types(np.int64(5))
    assert val == 5
    assert isinstance(val, int)


def test_convert_numpy_float():
    val = convert_numpy_types(np.float32(1.5))
    assert val == pytest.approx(1.5)
    assert isinstance(val, float)


def test_convert_numpy_array():
    val = convert_numpy_types(np.array([1, 2, 3]))
    assert val == [1, 2, 3]
    assert isinstance(val, list)


def test_convert_numpy_nested_dict():
    val = convert_numpy_types({'a': np.int32(10), 'b': [np.float64(0.5)]})
    assert val == {'a': 10, 'b': [0.5]}
    assert isinstance(val['a'], int)
    assert isinstance(val['b'][0], float)


def test_convert_python_types_unchanged():
    val = convert_numpy_types({'k': 'string', 'n': 7})
    assert val == {'k': 'string', 'n': 7}


def test_find_optimal_threshold_returns_sane_bounds(app):
    confidences = [0.1, 0.2, 0.4, 0.6, 0.8, 0.9]
    true_labels = [0, 0, 0, 1, 1, 1]
    with app.app_context():
        thr = find_optimal_threshold(confidences, true_labels, step=0.1)
    assert 0.0 <= float(thr) <= 1.0


def test_find_optimal_threshold_fallback_on_bad_input(app):
    with app.app_context():
        thr = find_optimal_threshold([], [], step=0.1)
    assert thr == 0.5

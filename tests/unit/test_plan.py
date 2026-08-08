import pytest

from hiltf.layer2_engine.plan import (
    hold_ok,
    timing_ok,
    tolerance_window,
    within_tolerance,
)


def test_tolerance_window():
    lo, hi = tolerance_window(200.0, 3.0)
    assert lo == pytest.approx(194.0)
    assert hi == pytest.approx(206.0)


@pytest.mark.parametrize(
    "measured,expected",
    [(194.0, True), (206.0, True), (193.9, False), (206.1, False)],
)
def test_within_tolerance(measured, expected):
    assert within_tolerance(measured, 200.0, 3.0) is expected


def test_timing_ok():
    assert timing_ok(1200.0, 2000.0) is True
    assert timing_ok(2500.0, 2000.0) is False
    assert timing_ok(0.0, 2000.0) is False  # no edge found


def test_hold_ok():
    assert hold_ok(3.08, 3.0) is True
    assert hold_ok(2.9, 3.0) is False

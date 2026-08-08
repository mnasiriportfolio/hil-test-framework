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


def test_timing_ok_ceiling():
    assert timing_ok(1200.0, max_ms=2000.0) is True
    assert timing_ok(2500.0, max_ms=2000.0) is False
    assert timing_ok(0.0, max_ms=2000.0) is False  # no edge found


def test_timing_ok_floor():
    """A slow protection states a minimum: tripping early is the failure.

    This is the case the old single-limit check could not express, so an
    inrush-tripping device passed a test written to catch exactly that.
    """
    assert timing_ok(1215.0, min_ms=1200.0) is True
    assert timing_ok(900.0, min_ms=1200.0) is False
    assert timing_ok(60_000.0, min_ms=1200.0) is True  # no ceiling was stated


def test_timing_ok_window():
    assert timing_ok(3.1, min_ms=1.0, max_ms=5.0) is True
    assert timing_ok(0.5, min_ms=1.0, max_ms=5.0) is False
    assert timing_ok(5.4, min_ms=1.0, max_ms=5.0) is False


def test_timing_ok_rejects_a_missing_edge():
    """``inf`` means "no edge was ever seen" — not "arrived very quickly"."""
    assert timing_ok(float("inf"), min_ms=1200.0) is False
    assert timing_ok(float("inf"), max_ms=2000.0) is False
    assert timing_ok(float("nan"), max_ms=2000.0) is False


def test_hold_ok():
    assert hold_ok(3.08, 3.0) is True
    assert hold_ok(2.9, 3.0) is False

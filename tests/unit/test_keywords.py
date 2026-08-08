"""The measurement maths in the keyword layer.

Pure functions over a Waveform — no bench, no config, no I/O. These are the
calculations a report's numbers come from, so they get tested at the level
where a mistake is visible.
"""

from __future__ import annotations

import math

import pytest

from hiltf.layer2_engine import (
    CalibrationError,
    auto_threshold_v,
    capture_gate_ms,
    edges_ms,
    pulse_width_ms,
    rising_edge_ms,
    vpp_for_current,
)
from hiltf.layer2_engine.plan import Scenario
from hiltf.layer3_hal import Waveform


def trace(dt_ms: float, pattern: list[tuple[float, int]]) -> Waveform:
    """Build a Waveform from ``(level, count)`` runs."""
    samples: list[float] = []
    for level, count in pattern:
        samples.extend([level] * count)
    return Waveform(dt_s=dt_ms / 1000.0, samples=samples)


# --- threshold -------------------------------------------------------------
def test_threshold_is_derived_from_the_trace():
    """So a 3.3 V, 5 V or 24 V relay line all work unconfigured."""
    assert auto_threshold_v(trace(1.0, [(0.0, 10), (3.3, 10)])) == pytest.approx(1.65)
    assert auto_threshold_v(trace(1.0, [(0.0, 10), (24.0, 10)])) == pytest.approx(12.0)


def test_a_flat_trace_has_no_threshold():
    """A relay that never moved must not yield an invented edge."""
    assert auto_threshold_v(trace(1.0, [(0.0, 100)])) is None
    assert auto_threshold_v(Waveform(dt_s=1e-3, samples=[])) is None


def test_noise_alone_does_not_look_like_an_edge():
    assert auto_threshold_v(trace(1.0, [(0.0, 10), (0.2, 10)])) is None


# --- edges -----------------------------------------------------------------
def test_edges_of_a_clean_pulse():
    wf = trace(1.0, [(0.0, 100), (3.3, 250), (0.0, 50)])
    rise, fall = edges_ms(wf)
    assert rise == pytest.approx(100.0)
    assert fall == pytest.approx(350.0)
    assert pulse_width_ms(wf) == pytest.approx(250.0)


def test_no_edge_reports_infinity_not_zero():
    """``inf`` fails a "<= limit" check; 0.0 would silently pass it."""
    wf = trace(1.0, [(0.0, 100)])
    assert rising_edge_ms(wf) == math.inf
    assert pulse_width_ms(wf) == 0.0


def test_falling_edge_is_only_searched_after_the_rise():
    """A trace that starts high must not read as a pulse that ended at t=0."""
    wf = trace(1.0, [(3.3, 20), (0.0, 30), (3.3, 40)])
    rise, fall = edges_ms(wf)
    assert rise == 0.0
    assert fall == pytest.approx(20.0)


def test_still_asserted_at_the_end_reports_what_was_seen():
    """A hold longer than the capture window still satisfies '>= 3 s'."""
    wf = trace(1.0, [(0.0, 100), (3.3, 900)])
    assert pulse_width_ms(wf) == pytest.approx(900.0)


def test_sub_millisecond_resolution():
    """50 kS/s resolves a 0.02 ms step — the claim the scope path rests on."""
    wf = trace(0.02, [(0.0, 211), (3.3, 100)])
    assert rising_edge_ms(wf) == pytest.approx(4.22, abs=0.01)


# --- capture window --------------------------------------------------------
def _scenario(**kwargs):
    fields = dict(
        name="x",
        enabled=True,
        trigger=200.0,
        tolerance_pct=3.0,
        detect_min_ms=0.0,
        detect_max_ms=float("inf"),
        relay_set_max_ms=5.0,
        hold_min_s=3.0,
        extra={},
    )
    fields.update(kwargs)
    return Scenario(**fields)


def test_gate_covers_detection_plus_hold():
    """Too short a gate truncates the pulse and understates the hold."""
    assert capture_gate_ms(_scenario(detect_max_ms=2000.0)) == pytest.approx(5500.0)


def test_gate_stays_finite_when_the_spec_states_no_ceiling():
    """A floor-only spec still needs a window, and a wide one.

    ``inf`` is a legitimate ceiling for "the spec is silent", but it cannot be
    handed to a scope. The gate opens well past the floor so a device that
    trips far too late is measured rather than mistaken for one that never
    tripped at all.
    """
    gate = capture_gate_ms(_scenario(detect_min_ms=1200.0))
    assert math.isfinite(gate)
    assert gate > 1200.0 + 3.0 * 1000.0


# --- drive levels ----------------------------------------------------------
def test_vpp_for_current_inverts_the_rms_relationship():
    assert vpp_for_current(200.0, 10.0) == pytest.approx(20.0 * 2.0 * math.sqrt(2.0))


@pytest.mark.parametrize("ratio", [0.0, -1.0])
def test_a_nonphysical_ratio_is_refused(ratio):
    """Better to stop than to compute a drive level from a bad calibration."""
    with pytest.raises(CalibrationError):
        vpp_for_current(200.0, ratio)

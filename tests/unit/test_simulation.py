import math

import pytest

from hiltf.layer3_hal import SimConfig, SimulatedBench


def _bench(**kwargs):
    cfg = SimConfig(volts_to_amps=10.0, **kwargs)
    return SimulatedBench(cfg, n_channels=2)


def _drive_current(bench, amplitude_vpp, frequency_hz=50.0):
    bench.configure_sine(2, amplitude_vpp, frequency_hz)
    bench.set_output(2, True)


def _energise(bench, kv=1.5):
    bench.configure_dc(1, kv / bench.cfg.volts_to_kilovolts)
    bench.set_output(1, True)


def test_sine_rms_to_current():
    bench = _bench()
    _drive_current(bench, 2.0)  # -> Vrms = 1/sqrt2 ~= 0.7071
    assert bench.current_rms_a() == (2.0 / (2.0 * math.sqrt(2.0))) * 10.0


def test_overcurrent_trip():
    bench = _bench(overcurrent_trigger_a=200.0)
    _drive_current(bench, 20.0 * 2.0 * math.sqrt(2.0))  # Vrms = 20
    assert bench.current_rms_a() == 200.0
    assert bench.overcurrent_tripped() is True


def test_slow_and_fast_detectors_compare_different_quantities():
    """The same stimulus is 300 A to one detector and 424 A to the other.

    A sine that reads 300 A RMS peaks at 300*sqrt2. The slow detector
    integrates and sees the former; the fast one compares instantaneously and
    sees the latter. Conflating them is a factor-of-root-two error in the
    quantity the specification is written against.
    """
    bench = _bench(overcurrent_trigger_a=200.0, fast_overcurrent_trigger_a=500.0)
    _energise(bench)
    _drive_current(bench, 30.0 * 2.0 * math.sqrt(2.0))  # 300 A RMS

    assert bench.current_rms_a() == pytest.approx(300.0)
    assert bench.current_peak_a() == pytest.approx(300.0 * math.sqrt(2.0))
    assert bench.overcurrent_tripped() is True
    assert bench.fast_overcurrent_tripped() is False  # 424 A peak is under 500 A


def test_fast_detection_includes_the_stimulus_phase_delay():
    """An instantaneous detector cannot respond before the sine arrives.

    Driven at a peak only just above the threshold, the crossing happens late
    in the quarter cycle; driven far above it, almost immediately. The delay
    belongs to the stimulus, which is why an AC measurement of an
    instantaneous-trip limit always reads high.
    """
    bench = _bench(fast_overcurrent_trigger_a=500.0, fast_overcurrent_detect_ms=0.0)
    _energise(bench)

    _drive_current(bench, (780.0 / 10.0) * 2.0)  # 780 A peak
    far_past = bench.detection_time_ms("fast_overcurrent")

    _drive_current(bench, (505.0 / 10.0) * 2.0)  # 505 A peak: only just over
    barely = bench.detection_time_ms("fast_overcurrent")

    assert 0.0 < far_past < barely < 5.0  # both inside a quarter cycle of 50 Hz


def test_harmonic_is_ac_content_on_a_dc_line():
    """Alternating current on a DC line is contamination; on an AC line it is the line."""
    bench = _bench(harmonic_trigger_a=1.5)
    _energise(bench)  # DC line
    _drive_current(bench, (1.6 / 10.0) * 2.0 * math.sqrt(2.0))
    assert bench.harmonic_current_rms_a() == pytest.approx(1.6)
    assert bench.harmonic_tripped() is True

    bench.configure_sine(1, 1.0, 50.0)  # the same current, now on an AC line
    assert bench.harmonic_current_rms_a() == 0.0
    assert bench.harmonic_tripped() is False


def test_line_detection_is_hysteretic():
    """Drop-out sits below pick-up, so a supply near the limit cannot chatter."""
    bench = _bench()
    c = bench.cfg
    _energise(bench, kv=1.5)
    assert bench.line_detected() is True

    between = (c.line_out_low_kv + c.line_in_low_kv) / 2.0
    bench.configure_dc(1, between / c.volts_to_kilovolts)
    assert bench.line_detected() is True  # below pick-up, still above drop-out

    bench.configure_dc(1, (c.line_out_low_kv * 0.9) / c.volts_to_kilovolts)
    assert bench.line_detected() is False


def test_short_supply_hole_is_ridden_through():
    bench = _bench()
    _energise(bench)
    bench.interrupt_output(1, bench.cfg.line_hole_ride_ms - 1.0)
    assert bench.line_detected() is True
    bench.interrupt_output(1, bench.cfg.line_hole_ride_ms + 1.0)
    assert bench.line_detected() is False


def test_pin_leads_the_contact_by_the_relay_set_time():
    """Two traces, one acquisition, and the gap between them is a real number."""
    bench = _bench(relay_set_ms=2.0, overcurrent_detect_ms=1215.0)
    _energise(bench)
    _drive_current(bench, 30.0 * 2.0 * math.sqrt(2.0))  # 300 A RMS

    rate = 50_000.0
    dt, pin = bench.line_trace("overcurrent_pin", 5_000.0, rate)
    _, contact = bench.line_trace("overcurrent", 5_000.0, rate)
    threshold = bench.cfg.relay_high_v / 2.0
    first = lambda s: next(i for i, v in enumerate(s) if v >= threshold)  # noqa: E731

    pin_ms = first(pin) * dt * 1000.0
    contact_ms = first(contact) * dt * 1000.0
    assert pin_ms == pytest.approx(1215.0, abs=0.05)
    assert contact_ms - pin_ms == pytest.approx(2.0, abs=0.05)


def test_release_capture_starts_asserted_and_opens_on_time():
    """A drop-out is its own acquisition, triggered on the supply going away.

    It is not the pick-up trace read later, and it does not depend on the bench
    having watched the transition happen — otherwise how often the test polled
    would change what the scope showed.
    """
    bench = _bench(line_out_ms=524.0, relay_set_ms=2.0)
    dt, contact = bench.line_trace("line_release", 2_000.0, 50_000.0)
    assert contact[0] > 0.0  # still closed when the sweep starts
    assert contact[-1] == 0.0  # and open by the end of it

    threshold = bench.cfg.relay_high_v / 2.0
    opened = next(i for i, v in enumerate(contact) if v < threshold) * dt * 1000.0
    assert opened == pytest.approx(524.0 + 2.0, abs=0.05)

    _, pin = bench.line_trace("line_release_pin", 2_000.0, 50_000.0)
    pin_opened = next(i for i, v in enumerate(pin) if v < threshold) * dt * 1000.0
    assert opened - pin_opened == pytest.approx(2.0, abs=0.05)


def test_analog_output_is_a_live_zero_current_loop():
    bench = _bench(
        analog_full_scale_kv=3.0,
        analog_full_scale_ma=20.0,
        analog_zero_ma=4.0,
        analog_error_ch1=0.0,
    )
    _energise(bench, kv=0.0)
    assert bench.analog_output_ma(1) == pytest.approx(4.0)  # zero is 4 mA, not 0
    _energise(bench, kv=3.0)
    assert bench.analog_output_ma(1) == pytest.approx(20.0)
    _energise(bench, kv=1.5)
    assert bench.analog_output_ma(1) == pytest.approx(12.0)


def test_analog_gain_error_does_not_scale_the_live_zero():
    """A gain error scales the signal; scaling the 4 mA floor too is an offset error.

    If the live zero were scaled as well, the reported error in kilovolts would
    grow without limit as the signal shrank, and a perfectly linear channel
    would look worse the lower it was driven.
    """
    bench = _bench(analog_error_ch1=0.01)
    for kv in (0.5, 1.0, 2.0):
        _energise(bench, kv=kv)
        span = bench.cfg.analog_full_scale_ma - bench.cfg.analog_zero_ma
        equivalent_kv = (
            (bench.analog_output_ma(1) - bench.cfg.analog_zero_ma)
            / span
            * bench.cfg.analog_full_scale_kv
        )
        assert (equivalent_kv - kv) / kv == pytest.approx(0.01)


def test_unknown_scope_line_is_rejected():
    bench = _bench()
    with pytest.raises(KeyError):
        bench.line_trace("not_a_line", 100.0, 50_000.0)

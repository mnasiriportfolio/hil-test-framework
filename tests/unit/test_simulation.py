import math

from hiltf.layer3_hal import SimConfig, SimulatedBench


def _bench():
    return SimulatedBench(SimConfig(volts_to_amps=10.0), n_channels=2)


def test_sine_rms_to_current():
    bench = _bench()
    ch = bench.channels[2]
    ch.enabled = True
    ch.waveform = "SIN"
    ch.amplitude_vpp = 2.0  # -> Vrms = 1/sqrt2 ~= 0.7071
    expected_a = (2.0 / (2.0 * math.sqrt(2.0))) * 10.0
    assert bench.current_rms_a() == expected_a


def test_overcurrent_trip():
    bench = SimulatedBench(SimConfig(volts_to_amps=10.0, overcurrent_trigger_a=200.0))
    ch = bench.channels[2]
    ch.enabled = True
    ch.waveform = "SIN"
    # need Vrms = 20 -> Vpp = 20*2*sqrt2
    ch.amplitude_vpp = 20.0 * 2.0 * math.sqrt(2.0)
    assert bench.current_rms_a() == 200.0
    assert bench.overcurrent_tripped() is True


def test_harmonic_rms_excludes_fundamental():
    bench = _bench()
    ch = bench.channels[2]
    ch.enabled = True
    ch.waveform = "SIN"
    ch.amplitude_vpp = 10.0
    ch.harmonics = {3: 2.0}
    fund_only = 10.0 / (2.0 * math.sqrt(2.0))
    harm_only = 2.0 / (2.0 * math.sqrt(2.0))
    assert bench.channels[2].harmonic_rms_v() == harm_only
    assert bench.channels[2].rms_v() == math.hypot(fund_only, harm_only)


def test_analog_output_gain_error_and_correction():
    bench = SimulatedBench(SimConfig(analog_output_gain_error=0.03))
    assert bench.analog_output_a(1, 100.0) == 103.0
    bench.analog_correction = 100.0 / 103.0
    assert round(bench.analog_output_a(1, 100.0), 6) == 100.0

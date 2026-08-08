"""SCPI command construction, block framing, and the emulator's interpreter.

No sockets here — the interpreter is deliberately pure, so the entire command
set is testable at unit speed. The socket layer gets its own integration test.
"""

from __future__ import annotations

import math
import struct

import pytest

from hiltf.emulators.scpi_server import ERR_UNDEFINED_HEADER, NO_ERROR, ScpiInterpreter
from hiltf.layer3_hal import scpi_commands as scpi
from hiltf.layer3_hal.simulation import SimConfig, SimulatedBench


@pytest.fixture()
def bench():
    return SimulatedBench(SimConfig(volts_to_amps=10.0), n_channels=2)


@pytest.fixture()
def interp(bench):
    return ScpiInterpreter(bench, sample_rate_hz=10_000.0)


def _reply(interp: ScpiInterpreter, command: str) -> str:
    raw = interp.execute(command)
    assert raw is not None, f"expected a reply to {command!r}"
    return raw.decode("ascii").strip()


# --- command builders ------------------------------------------------------
def test_command_builders_are_channel_addressed():
    assert scpi.set_amplitude_vpp(2, 1.5) == "SOUR2:VOLT 1.500000"
    assert scpi.set_output(1, True) == "OUTP1 ON"
    assert scpi.set_output(1, False) == "OUTP1 OFF"
    assert scpi.set_harmonic_vpp(2, 3, 0.5) == "SOUR2:HARM3:VOLT 0.500000"
    assert scpi.digitize_relay("overcurrent", 5500.0) == 'DIG:REL "overcurrent",5500.000'


# --- IEEE 488.2 definite-length blocks -------------------------------------
def test_block_round_trip():
    values = [0.0, 3.3, -1.25, 1e6]
    decoded = scpi.decode_block(scpi.encode_block(values))
    assert decoded == pytest.approx(values, rel=1e-6)


def test_block_header_describes_its_own_length():
    raw = scpi.encode_block([1.0, 2.0])  # 8 bytes of payload
    assert raw.startswith(b"#18"), raw[:6]


def test_block_survives_a_payload_containing_the_terminator():
    """The reason blocks are read by count and never by delimiter.

    float32 0x0A0A0A0A is a perfectly ordinary sample value whose bytes are
    four newlines. A reader that split on the terminator would truncate here.
    """
    sneaky = struct.unpack("<f", b"\x0a\x0a\x0a\x0a")[0]
    values = [1.0, sneaky, 2.0]
    raw = scpi.encode_block(values)
    assert b"\n" in raw
    assert scpi.decode_block(raw) == pytest.approx(values, rel=1e-6)


@pytest.mark.parametrize(
    "raw, match",
    [
        (b"1234", "missing '#'"),
        (b"#X4", "expected a digit"),
        (b"#18\x00\x00", "truncated"),
    ],
)
def test_malformed_blocks_raise(raw, match):
    with pytest.raises(ValueError, match=match):
        scpi.decode_block(raw)


# --- interpreter: the instrument side --------------------------------------
def test_idn_and_opc(interp):
    assert _reply(interp, scpi.idn()) == scpi.IDN
    assert _reply(interp, scpi.operation_complete()) == "1"


def test_generator_round_trip(interp):
    interp.execute(scpi.set_function(2, "SIN"))
    interp.execute(scpi.set_amplitude_vpp(2, 5.0))
    interp.execute(scpi.set_frequency_hz(2, 50.0))
    interp.execute(scpi.set_output(2, True))

    assert _reply(interp, scpi.get_function(2)) == "SIN"
    assert float(_reply(interp, scpi.get_amplitude_vpp(2))) == pytest.approx(5.0)
    assert float(_reply(interp, scpi.get_frequency_hz(2))) == pytest.approx(50.0)
    assert _reply(interp, scpi.get_output(2)) == "1"


def test_measurements_follow_the_stimulus(interp, bench):
    interp.execute(scpi.set_function(2, "SIN"))
    interp.execute(scpi.set_amplitude_vpp(2, 20.0 * 2.0 * math.sqrt(2.0)))
    interp.execute(scpi.set_output(2, True))
    assert float(_reply(interp, scpi.measure_ac_current())) == pytest.approx(200.0, rel=1e-6)


def test_harmonic_measurement_excludes_the_fundamental(interp):
    interp.execute(scpi.set_function(2, "SIN"))
    interp.execute(scpi.set_amplitude_vpp(2, 10.0))
    interp.execute(scpi.set_harmonic_vpp(2, 3, 2.0))
    interp.execute(scpi.set_output(2, True))
    harmonic = float(_reply(interp, scpi.measure_harmonic_current()))
    total = float(_reply(interp, scpi.measure_ac_current()))
    assert harmonic == pytest.approx((2.0 / (2 * math.sqrt(2))) * 10.0)
    assert total > harmonic


def test_clear_harmonics_takes_effect(interp):
    interp.execute(scpi.set_function(2, "SIN"))
    interp.execute(scpi.set_harmonic_vpp(2, 3, 2.0))
    interp.execute(scpi.set_output(2, True))
    assert float(_reply(interp, scpi.measure_harmonic_current())) > 0
    interp.execute(scpi.clear_harmonics(2))
    assert float(_reply(interp, scpi.measure_harmonic_current())) == pytest.approx(0.0)


def test_digitize_then_fetch(interp):
    interp.execute(scpi.set_function(2, "SIN"))
    interp.execute(scpi.set_amplitude_vpp(2, 30.0 * 2.0 * math.sqrt(2.0)))
    interp.execute(scpi.set_output(2, True))  # 300 A, well past the 200 A trigger

    interp.execute(scpi.digitize_relay("overcurrent", 5000.0))
    rate = float(_reply(interp, scpi.waveform_sample_rate()))
    points = int(_reply(interp, scpi.waveform_points()))
    assert rate == pytest.approx(10_000.0)
    assert points == 50_000  # 5 s at 10 kS/s

    raw = interp.execute(scpi.waveform_data())
    samples = scpi.decode_block(raw.rstrip(b"\n"))
    assert len(samples) == points
    assert max(samples) == pytest.approx(3.3)
    assert samples[0] == 0.0  # low before the trip


def test_unknown_header_gets_silence_and_an_error_entry(interp):
    """Exactly what an unsupported command does on a real instrument."""
    assert interp.execute("NOSUCH:THING?") is None
    assert _reply(interp, scpi.system_error()) == ERR_UNDEFINED_HEADER
    assert _reply(interp, scpi.system_error()) == NO_ERROR  # queue drained


def test_bad_channel_is_an_error_not_a_crash(interp):
    assert interp.execute(scpi.get_amplitude_vpp(99)) is None
    assert "out of range" in _reply(interp, scpi.system_error())


def test_reset_clears_the_bench(interp, bench):
    interp.execute(scpi.set_function(2, "SIN"))
    interp.execute(scpi.set_amplitude_vpp(2, 50.0))
    interp.execute(scpi.set_output(2, True))
    assert bench.current_rms_a() > 0
    interp.execute(scpi.reset())
    assert bench.current_rms_a() == 0.0
    assert _reply(interp, scpi.get_output(2)) == "0"

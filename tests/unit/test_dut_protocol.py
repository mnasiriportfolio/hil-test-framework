"""The DUT's binary protocol codec, and the device logic behind it.

Pure functions, so every framing decision is pinned without a socket. The tests
that matter most here are the *negative* ones: a codec that decodes garbage
into plausible numbers is worse than one that crashes, because the wrong number
reaches a report.
"""
from __future__ import annotations

import struct

import pytest

from hiltf.emulators.dut_server import DEFAULT_IDENT, VALID_CHANNELS, DutLogic
from hiltf.layer3_hal import dut_protocol as proto
from hiltf.layer3_hal.simulation import SimConfig, SimulatedBench


@pytest.fixture()
def bench():
    return SimulatedBench(SimConfig(volts_to_amps=10.0), n_channels=2)


@pytest.fixture()
def logic(bench):
    return DutLogic(bench)


# --- framing ---------------------------------------------------------------
def test_frame_round_trip():
    frame = proto.decode(proto.encode(proto.REQ_RELAYS, b"\x01\x02"))
    assert frame.msg_id == proto.REQ_RELAYS
    assert frame.payload == b"\x01\x02"


@pytest.mark.parametrize(
    "raw, match",
    [
        (b"", "too short"),
        (b"\xa5", "too short"),
        (b"\x00\x03", "bad protocol mark"),
    ],
)
def test_bad_frames_raise(raw, match):
    with pytest.raises(proto.ProtocolError, match=match):
        proto.decode(raw)


def test_numbers_are_little_endian_on_the_wire():
    """Pinned explicitly, because getting this wrong does not raise.

    Big-endian is the network convention, little-endian is what embedded
    firmware actually emits. A codec written to the convention decodes every
    field into a plausible, wrong value — and a test suite written against the
    same wrong assumption agrees with it perfectly.
    """
    raw = proto.encode_analog_response(1, 1.0)
    payload = proto.decode(raw).payload
    assert payload[1:] == struct.pack("<d", 1.0)
    assert payload[1:] != struct.pack(">d", 1.0)

    ident = proto.encode_ident_response(proto.Ident(1, 2, 0x01020304, "x"))
    assert struct.pack("<I", 0x01020304) in ident


# --- identity --------------------------------------------------------------
def test_ident_round_trip():
    original = proto.Ident(fw_major=2, fw_minor=4, serial=100_247, name="HILTF-SIM-DUT")
    decoded = proto.decode_ident_response(proto.decode(
        proto.encode_ident_response(original)).payload)
    assert decoded == original
    assert decoded.firmware == "2.4"


def test_truncated_ident_name_raises():
    raw = proto.decode(proto.encode_ident_response(proto.Ident(1, 0, 7, "abcdef"))).payload
    with pytest.raises(proto.ProtocolError, match="truncated"):
        proto.decode_ident_response(raw[:-3])


# --- relays ----------------------------------------------------------------
def test_relay_round_trip():
    states = {"overcurrent": True, "harmonic": False}
    payload = proto.decode(proto.encode_relay_response(states)).payload
    assert proto.decode_relay_response(payload) == states


def test_truncated_relay_list_raises():
    payload = proto.decode(proto.encode_relay_response({"a": True, "b": False})).payload
    with pytest.raises(proto.ProtocolError, match="truncated|missing state"):
        proto.decode_relay_response(payload[:-2])


def test_relay_count_mismatch_is_detected():
    """A count byte that promises more entries than the payload holds."""
    payload = bytearray(proto.decode(proto.encode_relay_response({"a": True})).payload)
    payload[0] = 5  # claim five relays, carry one
    with pytest.raises(proto.ProtocolError):
        proto.decode_relay_response(bytes(payload))


# --- analog + correction ---------------------------------------------------
def test_analog_round_trip():
    payload = proto.decode(proto.encode_analog_response(3, 51.5)).payload
    assert proto.decode_analog_response(payload) == (3, pytest.approx(51.5))


def test_correction_round_trip():
    payload = proto.decode(proto.encode_correction_request(0.9709)).payload
    assert proto.decode_correction_request(payload) == pytest.approx(0.9709)


@pytest.mark.parametrize(
    "decoder, payload",
    [
        (proto.decode_analog_response, b"\x01\x00"),
        (proto.decode_correction_request, b"\x00" * 4),
        (proto.decode_ack, b""),
        (proto.decode_nack, b"\x01"),
    ],
)
def test_short_payloads_raise_rather_than_unpack_garbage(decoder, payload):
    with pytest.raises(proto.ProtocolError, match="expected"):
        decoder(payload)


# --- NACK ------------------------------------------------------------------
def test_nack_carries_a_reason():
    frame = proto.decode(proto.encode_nack(proto.REQ_ANALOG, proto.NACK_BAD_CHANNEL))
    with pytest.raises(proto.NackError, match="channel does not exist") as exc:
        proto.raise_for_nack(frame)
    assert exc.value.reason_code == proto.NACK_BAD_CHANNEL
    assert exc.value.echoed_msg_id == proto.REQ_ANALOG


def test_raise_for_nack_ignores_normal_frames():
    proto.raise_for_nack(proto.decode(proto.encode_relay_response({})))


# --- device logic ----------------------------------------------------------
def test_logic_answers_identity(logic):
    frame = proto.decode(logic.execute(proto.decode(proto.encode(proto.REQ_IDENT))))
    assert proto.decode_ident_response(frame.payload) == DEFAULT_IDENT


def test_logic_reports_relays_caused_by_the_stimulus(logic, bench):
    bench.configure_sine(2, 30.0 * 2.0 * (2 ** 0.5), 50.0)
    bench.set_output(2, True)
    frame = proto.decode(logic.execute(proto.decode(proto.encode(proto.REQ_RELAYS))))
    assert proto.decode_relay_response(frame.payload)["overcurrent"] is True


def test_logic_nacks_an_invalid_channel(logic):
    request = proto.decode(proto.encode_analog_request(99))
    frame = proto.decode(logic.execute(request))
    assert frame.msg_id == proto.RSP_NACK
    assert proto.decode_nack(frame.payload) == (proto.REQ_ANALOG, proto.NACK_BAD_CHANNEL)


@pytest.mark.parametrize("channel", VALID_CHANNELS)
def test_logic_accepts_every_declared_channel(logic, channel):
    frame = proto.decode(logic.execute(proto.decode(proto.encode_analog_request(channel))))
    assert frame.msg_id == proto.RSP_ANALOG
    assert proto.decode_analog_response(frame.payload)[0] == channel


@pytest.mark.parametrize("factor", [0.0, -1.0, 1e6])
def test_logic_refuses_a_nonsensical_correction(logic, factor):
    """A bad calibration factor would silently corrupt every later reading."""
    frame = proto.decode(logic.execute(proto.decode(proto.encode_correction_request(factor))))
    assert frame.msg_id == proto.RSP_NACK
    assert proto.decode_nack(frame.payload)[1] == proto.NACK_BAD_VALUE


def test_logic_accepts_a_sane_correction(logic, bench):
    frame = proto.decode(logic.execute(proto.decode(proto.encode_correction_request(0.97))))
    assert frame.msg_id == proto.RSP_ACK
    assert proto.decode_ack(frame.payload) == proto.REQ_SET_CORRECTION
    assert bench.analog_correction == pytest.approx(0.97)


def test_logic_nacks_an_unknown_message(logic):
    frame = proto.decode(logic.execute(proto.decode(proto.encode(0x7E))))
    assert frame.msg_id == proto.RSP_NACK
    assert proto.decode_nack(frame.payload) == (0x7E, proto.NACK_UNKNOWN_MESSAGE)

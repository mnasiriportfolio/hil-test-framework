"""Layer 3 (HAL) — the DUT's binary protocol codec.

Instruments speak SCPI. Devices under test usually do not: they speak whatever
their firmware team wrote, over UDP or a serial line, in fixed-width binary
fields. This module is that kind of protocol — invented for this project, but
built to the shape the real ones have — kept **pure** so every framing decision
is unit-testable without a socket, a thread or a device.

Frame layout, little-endian throughout::

    +--------+--------+------------------------+
    | 0xA5   | msg_id | payload (msg-specific)  |
    +--------+--------+------------------------+
      1 byte   1 byte   0..n bytes

Three decisions here are the ones that actually cost time on real hardware:

**Endianness is little, and it is stated.** Network byte order is big-endian by
convention, so a codec written from habit reaches for ``!`` or ``ntohl`` — but
embedded firmware almost always emits its CPU's native order, which is
little. Getting this wrong does not raise: it returns plausible, wrong numbers.
A test suite written against a *guessed* endianness will agree with itself
perfectly and be wrong about the device.

**Length is validated before unpacking.** A short datagram must raise, not
return whatever ``struct`` finds. A silent decode of a truncated frame is how a
measurement error becomes a passing test.

**A NACK is a first-class response, not an exception in disguise.** The device
is allowed to refuse — the request is out of range, the channel does not exist,
an interlock is engaged. The caller needs the reason code to decide whether to
retry, skip or fail, so it is carried in the reply rather than flattened.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

#: Marks the start of every frame; a cheap guard against a stray datagram on a
#: shared port being decoded as a response.
PROTOCOL_MARK = 0xA5

# --- message ids ----------------------------------------------------------
REQ_IDENT = 0x01
RSP_IDENT = 0x02
REQ_RELAYS = 0x03
RSP_RELAYS = 0x04
REQ_ANALOG = 0x05
RSP_ANALOG = 0x06
REQ_SET_CORRECTION = 0x07
RSP_ACK = 0x08
RSP_NACK = 0xFF

# --- NACK reason codes ----------------------------------------------------
NACK_UNKNOWN_MESSAGE = 0x01
NACK_BAD_LENGTH = 0x02
NACK_BAD_CHANNEL = 0x03
NACK_BAD_VALUE = 0x04

NACK_REASONS = {
    NACK_UNKNOWN_MESSAGE: "unknown message id",
    NACK_BAD_LENGTH: "payload length invalid for this message",
    NACK_BAD_CHANNEL: "channel does not exist",
    NACK_BAD_VALUE: "value out of range",
}

# Little-endian, explicitly. See the module docstring.
_LE_U8 = "<B"
_LE_U32 = "<I"
_LE_F64 = "<d"


class ProtocolError(ValueError):
    """A frame could not be decoded — malformed, truncated or unexpected."""


class NackError(RuntimeError):
    """The device refused the request and said why."""

    def __init__(self, echoed_msg_id: int, reason_code: int) -> None:
        reason = NACK_REASONS.get(reason_code, f"unknown reason 0x{reason_code:02X}")
        super().__init__(f"device NACKed message 0x{echoed_msg_id:02X}: {reason}")
        self.echoed_msg_id = echoed_msg_id
        self.reason_code = reason_code


@dataclass(frozen=True)
class Frame:
    msg_id: int
    payload: bytes


@dataclass(frozen=True)
class Ident:
    fw_major: int
    fw_minor: int
    serial: int
    name: str

    @property
    def firmware(self) -> str:
        return f"{self.fw_major}.{self.fw_minor}"


# --- framing --------------------------------------------------------------
def encode(msg_id: int, payload: bytes = b"") -> bytes:
    if not 0 <= msg_id <= 0xFF:
        raise ProtocolError(f"msg_id out of range: {msg_id}")
    return bytes((PROTOCOL_MARK, msg_id)) + payload


def decode(raw: bytes) -> Frame:
    if len(raw) < 2:
        raise ProtocolError(f"frame too short: {len(raw)} byte(s), need at least 2")
    if raw[0] != PROTOCOL_MARK:
        raise ProtocolError(f"bad protocol mark: 0x{raw[0]:02X}, expected 0x{PROTOCOL_MARK:02X}")
    return Frame(msg_id=raw[1], payload=raw[2:])


def _require_length(payload: bytes, expected: int, what: str) -> None:
    if len(payload) != expected:
        raise ProtocolError(f"{what}: expected {expected} payload byte(s), got {len(payload)}")


# --- identity -------------------------------------------------------------
def encode_ident_response(ident: Ident) -> bytes:
    name = ident.name.encode("utf-8")
    if len(name) > 0xFF:
        raise ProtocolError("device name too long for a single-byte length field")
    return encode(
        RSP_IDENT,
        struct.pack(_LE_U8, ident.fw_major)
        + struct.pack(_LE_U8, ident.fw_minor)
        + struct.pack(_LE_U32, ident.serial)
        + struct.pack(_LE_U8, len(name))
        + name,
    )


def decode_ident_response(payload: bytes) -> Ident:
    if len(payload) < 7:
        raise ProtocolError(f"ident: header needs 7 bytes, got {len(payload)}")
    fw_major = payload[0]
    fw_minor = payload[1]
    (serial,) = struct.unpack_from(_LE_U32, payload, 2)
    name_len = payload[6]
    name = payload[7 : 7 + name_len]
    if len(name) != name_len:
        raise ProtocolError(f"ident: name truncated ({len(name)} of {name_len} bytes)")
    return Ident(fw_major, fw_minor, serial, name.decode("utf-8", errors="replace"))


# --- relay states ---------------------------------------------------------
def encode_relay_response(states: dict[str, bool]) -> bytes:
    if len(states) > 0xFF:
        raise ProtocolError("too many relays for a single-byte count field")
    body = bytearray(struct.pack(_LE_U8, len(states)))
    for name, state in states.items():
        raw_name = name.encode("utf-8")
        if len(raw_name) > 0xFF:
            raise ProtocolError(f"relay name too long: {name!r}")
        body += struct.pack(_LE_U8, len(raw_name)) + raw_name
        body += struct.pack(_LE_U8, 1 if state else 0)
    return encode(RSP_RELAYS, bytes(body))


def decode_relay_response(payload: bytes) -> dict[str, bool]:
    if not payload:
        raise ProtocolError("relay response: empty payload")
    count = payload[0]
    states: dict[str, bool] = {}
    pos = 1
    for _ in range(count):
        if pos >= len(payload):
            raise ProtocolError(f"relay response: truncated after {len(states)} of {count} entries")
        name_len = payload[pos]
        pos += 1
        name = payload[pos : pos + name_len]
        if len(name) != name_len:
            raise ProtocolError("relay response: name truncated")
        pos += name_len
        if pos >= len(payload):
            raise ProtocolError(f"relay response: missing state byte for {name!r}")
        states[name.decode("utf-8", errors="replace")] = bool(payload[pos])
        pos += 1
    return states


# --- analog output --------------------------------------------------------
def encode_analog_request(channel: int) -> bytes:
    if not 0 <= channel <= 0xFF:
        raise ProtocolError(f"channel out of range: {channel}")
    return encode(REQ_ANALOG, struct.pack(_LE_U8, channel))


def decode_analog_request(payload: bytes) -> int:
    _require_length(payload, 1, "analog request")
    return payload[0]


def encode_analog_response(channel: int, value: float) -> bytes:
    return encode(RSP_ANALOG, struct.pack(_LE_U8, channel) + struct.pack(_LE_F64, value))


def decode_analog_response(payload: bytes) -> tuple[int, float]:
    _require_length(payload, 9, "analog response")
    (value,) = struct.unpack_from(_LE_F64, payload, 1)
    return payload[0], value


# --- calibration correction ------------------------------------------------
def encode_correction_request(factor: float) -> bytes:
    return encode(REQ_SET_CORRECTION, struct.pack(_LE_F64, factor))


def decode_correction_request(payload: bytes) -> float:
    _require_length(payload, 8, "correction request")
    (factor,) = struct.unpack(_LE_F64, payload)
    return factor


# --- acknowledgement ------------------------------------------------------
def encode_ack(echoed_msg_id: int) -> bytes:
    return encode(RSP_ACK, struct.pack(_LE_U8, echoed_msg_id))


def decode_ack(payload: bytes) -> int:
    _require_length(payload, 1, "ack")
    return payload[0]


def encode_nack(echoed_msg_id: int, reason_code: int) -> bytes:
    return encode(RSP_NACK, struct.pack(_LE_U8, echoed_msg_id) + struct.pack(_LE_U8, reason_code))


def decode_nack(payload: bytes) -> tuple[int, int]:
    _require_length(payload, 2, "nack")
    return payload[0], payload[1]


def raise_for_nack(frame: Frame) -> None:
    """Turn a NACK frame into :class:`NackError`; do nothing otherwise."""
    if frame.msg_id == RSP_NACK:
        echoed, reason = decode_nack(frame.payload)
        raise NackError(echoed, reason)

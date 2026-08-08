"""Layer 3 (HAL) — the SCPI command set, defined exactly once.

Two different drivers speak this dialect (``lan_drivers`` over a raw TCP socket,
``visa_drivers`` over PyVISA) and one emulator answers it
(``hiltf.emulators.scpi_server``). If the command strings lived in three places
they would drift, and a driver would be "tested" against an emulator that had
quietly agreed with its own mistake. So the strings are built here, by
functions, and all three import them.

The dialect follows SCPI-99 conventions: hierarchical colon-separated headers,
a ``?`` suffix for queries, ``OUTPut``/``SOURce``/``MEASure`` subsystems, and
IEEE 488.2 definite-length blocks for bulk waveform transfer. It is modelled on
the command sets of mid-range benchtop generators and DMMs, without copying any
specific vendor's proprietary extensions.
"""

from __future__ import annotations

import struct

TERMINATOR = "\n"
IDN = "HILTF,BENCH-EMULATOR,0,1.0.0"

#: Waveform samples are transferred as little-endian IEEE-754 single precision.
BLOCK_DATATYPE = "f"
BLOCK_IS_BIG_ENDIAN = False
_BLOCK_STRUCT = "<f"


# --- common ---------------------------------------------------------------
def idn() -> str:
    return "*IDN?"


def reset() -> str:
    return "*RST"


def clear_status() -> str:
    return "*CLS"


def system_error() -> str:
    return "SYST:ERR?"


def operation_complete() -> str:
    """``*OPC?`` — the SCPI synchronisation point.

    Writes are fire-and-forget, and two instruments are two independent
    sessions with no ordering between them. So "set the generator, then read
    the meter" is a race unless something forces the generator to finish
    first. ``*OPC?`` is that something: it does not return until every command
    queued before it on the *same* session has been executed.

    Skip it and the test still passes on a fast day — which is the worst
    possible failure mode, because it comes back as a flake months later.
    """
    return "*OPC?"


# --- signal generator -----------------------------------------------------
def set_function(channel: int, shape: str) -> str:
    """``shape`` is ``SIN`` or ``DC``."""
    return f"SOUR{channel:d}:FUNC {shape.upper()}"


def get_function(channel: int) -> str:
    return f"SOUR{channel:d}:FUNC?"


def set_amplitude_vpp(channel: int, vpp: float) -> str:
    return f"SOUR{channel:d}:VOLT {vpp:.6f}"


def get_amplitude_vpp(channel: int) -> str:
    return f"SOUR{channel:d}:VOLT?"


def set_offset_v(channel: int, volts: float) -> str:
    return f"SOUR{channel:d}:VOLT:OFFS {volts:.6f}"


def get_offset_v(channel: int) -> str:
    return f"SOUR{channel:d}:VOLT:OFFS?"


def set_frequency_hz(channel: int, hz: float) -> str:
    return f"SOUR{channel:d}:FREQ {hz:.6f}"


def get_frequency_hz(channel: int) -> str:
    return f"SOUR{channel:d}:FREQ?"


def set_harmonic_vpp(channel: int, order: int, vpp: float) -> str:
    return f"SOUR{channel:d}:HARM{order:d}:VOLT {vpp:.6f}"


def clear_harmonics(channel: int) -> str:
    return f"SOUR{channel:d}:HARM:CLE"


def set_output(channel: int, on: bool) -> str:
    return f"OUTP{channel:d} {'ON' if on else 'OFF'}"


def get_output(channel: int) -> str:
    return f"OUTP{channel:d}?"


def interrupt_output(channel: int, duration_ms: float) -> str:
    """Drop an output for a stated number of milliseconds, then restore it."""
    return f"OUTP{channel:d}:INT {duration_ms:.3f}"


# --- multimeter -----------------------------------------------------------
def measure_dc_voltage() -> str:
    return "MEAS:VOLT:DC?"


def measure_line_kv() -> str:
    return "MEAS:VOLT:LINE?"


def measure_ac_current() -> str:
    return "MEAS:CURR:AC?"


def measure_harmonic_current() -> str:
    return "MEAS:CURR:AC:HARM?"


# --- oscilloscope ---------------------------------------------------------
def digitize_relay(relay: str, gate_ms: float) -> str:
    """Arm and acquire a named DUT line for ``gate_ms`` milliseconds.

    The name is any trace the bench can render — the injected stimulus, a
    detector's digital output pin, or the relay contact it drives. Detection
    time and relay set time are different edges on different lines, so the
    verb takes a name rather than assuming there is only one thing to look at.
    """
    return f'DIG:REL "{relay}",{gate_ms:.3f}'


def waveform_sample_rate() -> str:
    return "WAV:SRAT?"


def waveform_points() -> str:
    return "WAV:POIN?"


def waveform_data() -> str:
    return "WAV:DATA?"


# --- IEEE 488.2 definite-length block --------------------------------------
def encode_block(values: list[float]) -> bytes:
    """Encode floats as ``#<ndigits><bytecount><payload>``.

    This is the standard bulk-transfer envelope every benchtop scope uses:
    one digit saying how many digits the length has, the length, then raw
    samples. ASCII would be ~8x larger and an order of magnitude slower for
    the 290k-sample captures this framework takes.
    """
    payload = b"".join(struct.pack(_BLOCK_STRUCT, float(v)) for v in values)
    length = str(len(payload))
    return f"#{len(length)}{length}".encode("ascii") + payload


def block_header_length(first_byte: bytes) -> int:
    """Number of digits in the length field, given the byte after ``#``."""
    if not first_byte.isdigit():
        raise ValueError(f"malformed block header: expected a digit, got {first_byte!r}")
    return int(first_byte)


def decode_payload(payload: bytes) -> list[float]:
    """Decode the raw sample bytes of a block (header already stripped).

    Kept separate from :func:`decode_block` because a socket reader knows the
    byte count from the header and streams the payload straight in, while an
    in-memory test has the whole frame.
    """
    if len(payload) % 4:
        raise ValueError(f"block length {len(payload)} is not a whole number of float32")
    return [v[0] for v in struct.iter_unpack(_BLOCK_STRUCT, payload)]


def decode_block(raw: bytes) -> list[float]:
    """Inverse of :func:`encode_block`. Raises ``ValueError`` on a bad frame."""
    if not raw.startswith(b"#"):
        raise ValueError(f"malformed block: missing '#' prefix (got {raw[:8]!r})")
    ndigits = block_header_length(raw[1:2])
    start = 2 + ndigits
    declared = int(raw[2:start])
    payload = raw[start : start + declared]
    if len(payload) != declared:
        raise ValueError(f"truncated block: declared {declared} bytes, got {len(payload)}")
    return decode_payload(payload)

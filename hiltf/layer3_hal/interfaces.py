"""Layer 3 (HAL) — instrument interfaces.

These Protocols are the *only* vocabulary Layer 2 is allowed to speak. A driver
is any object that satisfies one of these Protocols, regardless of whether it
reaches the instrument through an in-process simulation, a raw TCP socket
speaking SCPI, PyVISA (``pyvisa-sim`` / ``pyvisa-py`` / NI-VISA), or a binary
UDP protocol. Swapping transports is a config change, never a Layer 2 change.

The repository ships four independent implementations of these same Protocols
(see ``hiltf/layer3_hal/``), and the identical Robot Framework suites run over
all of them. That is the whole point of the layer: Layer 2 has never been told
which one it is holding.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class Waveform:
    """A captured oscilloscope trace: evenly spaced samples starting at t=0."""

    dt_s: float
    samples: list[float] = field(default_factory=list)

    def times_s(self) -> list[float]:
        return [i * self.dt_s for i in range(len(self.samples))]

    @property
    def duration_s(self) -> float:
        return self.dt_s * len(self.samples)

    @property
    def sample_rate_hz(self) -> float:
        return 1.0 / self.dt_s if self.dt_s else 0.0


@runtime_checkable
class Instrument(Protocol):
    """Common lifecycle contract. Every driver is a context manager."""

    name: str

    def connect(self) -> None: ...
    def disconnect(self) -> None: ...
    def identify(self) -> str: ...
    def __enter__(self) -> Instrument: ...
    def __exit__(self, *exc: object) -> None: ...


@runtime_checkable
class SignalGenerator(Instrument, Protocol):
    """Drives stimulus onto the bench. Channels are 1-based ints."""

    def configure_sine(
        self,
        channel: int,
        amplitude_vpp: float,
        frequency_hz: float,
        offset_v: float = 0.0,
    ) -> None: ...
    def configure_dc(self, channel: int, level_v: float) -> None: ...
    def add_harmonic(self, channel: int, order: int, amplitude_vpp: float) -> None: ...
    def output_on(self, channel: int) -> None: ...
    def output_off(self, channel: int) -> None: ...
    def output_state(self, channel: int) -> bool: ...


@runtime_checkable
class Multimeter(Instrument, Protocol):
    """Reads back scalar measurements from the bench."""

    def measure_dc_voltage(self) -> float: ...
    def measure_ac_current_rms(self) -> float: ...
    def measure_harmonic_current_rms(self) -> float:
        """RMS of the harmonic content only, excluding the fundamental.

        On a physical bench this is a band-RMS reading (power analyser, or the
        DUT's own harmonic-band register). It is on the Multimeter Protocol so
        that Layer 2 never has to reach past the HAL to get it.
        """
        ...


@runtime_checkable
class Oscilloscope(Instrument, Protocol):
    """Captures time-domain traces — used for sub-millisecond relay timing."""

    def capture_relay(self, relay: str, gate_ms: float) -> Waveform:
        """Capture the DUT relay line from t=0 (stimulus applied).

        One capture carries BOTH measurements the spec asks for: the rising
        edge gives detection latency, and the width between the rising and
        falling edges gives the latch/hold duration. Layer 2 does the edge
        finding — the driver only moves samples.
        """
        ...


@runtime_checkable
class DeviceUnderTest(Instrument, Protocol):
    """The DUT's own control/telemetry channel (mirrors a vendor tool)."""

    def get_relay_states(self) -> dict[str, bool]: ...
    def read_analog_output(self, channel: int) -> float: ...
    def apply_analog_correction(self, factor: float) -> None: ...

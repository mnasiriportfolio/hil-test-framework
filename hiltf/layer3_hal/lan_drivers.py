"""Layer 3 (HAL) — LAN instrument drivers over raw TCP/SCPI.

These satisfy exactly the same Protocols as the in-process simulated drivers
and the PyVISA drivers. Layer 2 is not told which it has.

Transport: :class:`~hiltf.layer3_hal.scpi_socket.ScpiSocket` — stdlib sockets,
no PyVISA and no vendor runtime. Each driver holds its own TCP connection, as
each instrument in a real rack is its own listener.

Run them against ``python -m hiltf.emulators`` (or the ``bench`` service in
``docker-compose.yml``) to exercise the full socket path with no hardware.
"""
from __future__ import annotations

from .base_driver import BaseDriver
from .interfaces import Waveform
from .scpi_commands import (
    clear_harmonics,
    digitize_relay,
    get_output,
    idn,
    measure_ac_current,
    measure_dc_voltage,
    measure_harmonic_current,
    set_amplitude_vpp,
    set_frequency_hz,
    set_function,
    set_harmonic_vpp,
    set_offset_v,
    set_output,
    waveform_data,
    waveform_sample_rate,
)
from .scpi_socket import ScpiSocket


class _LanDriver(BaseDriver):
    """Shared connect/disconnect for anything reached over a SCPI socket."""

    def __init__(
        self,
        host: str,
        port: int = 5025,
        timeout_s: float = 10.0,
        name: str = "LAN",
    ) -> None:
        super().__init__(name)
        self.io = ScpiSocket(host, port, timeout_s=timeout_s, name=name)

    def connect(self) -> None:
        self.io.open()
        super().connect()

    def disconnect(self) -> None:
        try:
            self.io.close()
        finally:
            super().disconnect()

    def identify(self) -> str:
        self._require_connection()
        return self.io.safe_query(idn(), default="N/A")


class LanSignalGenerator(_LanDriver):
    def __init__(self, host: str, port: int = 5025, timeout_s: float = 10.0,
                 name: str = "LAN-AFG") -> None:
        super().__init__(host, port, timeout_s, name)

    def configure_sine(
        self, channel: int, amplitude_vpp: float, frequency_hz: float, offset_v: float = 0.0
    ) -> None:
        self._require_connection()
        # Order matters on real generators: shape first, then the parameters
        # that only exist for that shape. Harmonics are cleared explicitly so a
        # previous scenario's injection cannot leak into this one.
        self.io.write(set_function(channel, "SIN"))
        self.io.write(clear_harmonics(channel))
        self.io.write(set_amplitude_vpp(channel, amplitude_vpp))
        self.io.write(set_frequency_hz(channel, frequency_hz))
        self.io.write(set_offset_v(channel, offset_v))
        # Commit before returning: the caller's next act is usually to read a
        # different instrument, and nothing else orders the two sessions.
        self.io.sync()

    def configure_dc(self, channel: int, level_v: float) -> None:
        self._require_connection()
        self.io.write(set_function(channel, "DC"))
        self.io.write(clear_harmonics(channel))
        self.io.write(set_amplitude_vpp(channel, level_v))
        self.io.sync()

    def add_harmonic(self, channel: int, order: int, amplitude_vpp: float) -> None:
        self._require_connection()
        self.io.write(set_harmonic_vpp(channel, order, amplitude_vpp))
        self.io.sync()

    def output_on(self, channel: int) -> None:
        self._require_connection()
        self.io.write(set_output(channel, True))
        self.io.sync()

    def output_off(self, channel: int) -> None:
        self._require_connection()
        self.io.write(set_output(channel, False))
        self.io.sync()

    def output_state(self, channel: int) -> bool:
        self._require_connection()
        return self.io.query(get_output(channel)).strip() in {"1", "ON"}


class LanMultimeter(_LanDriver):
    def __init__(self, host: str, port: int = 5025, timeout_s: float = 10.0,
                 name: str = "LAN-DMM") -> None:
        super().__init__(host, port, timeout_s, name)

    def measure_dc_voltage(self) -> float:
        self._require_connection()
        return self.io.query_float(measure_dc_voltage())

    def measure_ac_current_rms(self) -> float:
        self._require_connection()
        return self.io.query_float(measure_ac_current())

    def measure_harmonic_current_rms(self) -> float:
        self._require_connection()
        return self.io.query_float(measure_harmonic_current())


class LanOscilloscope(_LanDriver):
    def __init__(self, host: str, port: int = 5025, timeout_s: float = 30.0,
                 name: str = "LAN-OSC") -> None:
        # A gated capture plus a multi-megabyte block transfer takes longer than
        # a scalar query, so this driver gets a longer default timeout.
        super().__init__(host, port, timeout_s, name)

    def capture_relay(self, relay: str, gate_ms: float) -> Waveform:
        """Arm, acquire, then pull the trace back as a binary block.

        The sample rate is *asked for*, never assumed: the instrument decides
        what it could actually achieve for the requested gate, and the timebase
        of the returned Waveform has to reflect that or every edge time
        computed from it is wrong.
        """
        self._require_connection()
        self.io.write(digitize_relay(relay, gate_ms))
        sample_rate = self.io.query_float(waveform_sample_rate())
        samples = self.io.query_block(waveform_data())
        return Waveform(dt_s=1.0 / sample_rate, samples=samples)

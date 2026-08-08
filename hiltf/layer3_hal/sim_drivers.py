"""Layer 3 (HAL) — in-process simulated drivers.

Four drivers that share one ``SimulatedBench`` by reference. They satisfy the
Layer 3 Protocols in ``interfaces.py``, so Layer 2 cannot tell them apart from
the socket, PyVISA or UDP drivers that sit beside them in this package.

This is the zero-dependency transport: no network, no PyVISA, no container.
It is the default bench so that ``git clone && pytest`` works on any machine.
"""

from __future__ import annotations

from .base_driver import BaseDriver
from .interfaces import Waveform
from .simulation import SimulatedBench


class SimSignalGenerator(BaseDriver):
    def __init__(self, bench: SimulatedBench, name: str = "SIM-AFG") -> None:
        super().__init__(name)
        self.bench = bench

    def identify(self) -> str:
        return "HILTF,SIM-AFG,0,1.0.0"

    def configure_sine(
        self, channel: int, amplitude_vpp: float, frequency_hz: float, offset_v: float = 0.0
    ) -> None:
        self._require_connection()
        self.bench.configure_sine(channel, amplitude_vpp, frequency_hz, offset_v)

    def configure_dc(self, channel: int, level_v: float) -> None:
        self._require_connection()
        self.bench.configure_dc(channel, level_v)

    def add_harmonic(self, channel: int, order: int, amplitude_vpp: float) -> None:
        self._require_connection()
        self.bench.add_harmonic(channel, order, amplitude_vpp)

    def output_on(self, channel: int) -> None:
        self._require_connection()
        self.bench.set_output(channel, True)

    def output_off(self, channel: int) -> None:
        self._require_connection()
        self.bench.set_output(channel, False)

    def output_state(self, channel: int) -> bool:
        self._require_connection()
        return self.bench.output_state(channel)


class SimMultimeter(BaseDriver):
    def __init__(self, bench: SimulatedBench, name: str = "SIM-DMM") -> None:
        super().__init__(name)
        self.bench = bench

    def identify(self) -> str:
        return "HILTF,SIM-DMM,0,1.0.0"

    def measure_dc_voltage(self) -> float:
        self._require_connection()
        return self.bench.voltage_dc_v()

    def measure_ac_current_rms(self) -> float:
        self._require_connection()
        return self.bench.current_rms_a()

    def measure_harmonic_current_rms(self) -> float:
        self._require_connection()
        return self.bench.harmonic_current_rms_a()


class SimOscilloscope(BaseDriver):
    def __init__(
        self, bench: SimulatedBench, sample_rate_hz: float = 50_000.0, name: str = "SIM-OSC"
    ) -> None:
        super().__init__(name)
        self.bench = bench
        self.sample_rate_hz = sample_rate_hz

    def identify(self) -> str:
        return "HILTF,SIM-OSC,0,1.0.0"

    def capture_relay(self, relay: str, gate_ms: float) -> Waveform:
        """Return the relay line: 0 V, then high at the trip, then low again.

        This is what makes 'oscilloscope-based timing' real in code: the keyword
        layer finds the rising edge to measure detection latency, and the width
        to the falling edge to measure the latch duration — exactly as on a
        physical bench, and from a single acquisition.
        """
        self._require_connection()
        dt, samples = self.bench.relay_trace(relay, gate_ms, self.sample_rate_hz)
        return Waveform(dt_s=dt, samples=samples)


class SimDeviceUnderTest(BaseDriver):
    def __init__(self, bench: SimulatedBench, name: str = "SIM-DUT") -> None:
        super().__init__(name)
        self.bench = bench

    def identify(self) -> str:
        return "HILTF,SIM-DUT,0,1.0.0"

    def get_relay_states(self) -> dict[str, bool]:
        self._require_connection()
        return self.bench.relay_states()

    def read_analog_output(self, channel: int) -> float:
        self._require_connection()
        # The DUT is asked to reproduce whatever current is currently driven.
        return self.bench.read_analog_output(channel)

    def apply_analog_correction(self, factor: float) -> None:
        """Write a calibration correction into the DUT (self-cal path)."""
        self._require_connection()
        self.bench.analog_correction = factor

"""Layer 3 (HAL) — the simulation backend.

``SimulatedBench`` is a single model of the stimulus + DUT. Driving the
generator mutates the bench; the multimeter, oscilloscope and DUT read from it.
Everything is closed-form and deterministic (no randomness) so unit tests and CI
are stable.

**This class is the one and only source of bench behaviour in the project.**
The in-process drivers hold it by reference; the SCPI-over-TCP emulator and the
binary-UDP DUT emulator hold the *same* object and expose it over the network.
So a result obtained through a socket is identical to one obtained in-process —
which is exactly the property that makes "the transport is a config choice"
a testable claim rather than a slogan.

The physics is intentionally simple but faithful in shape to a real bench:
* a sine of peak-to-peak ``A`` has RMS ``A / (2*sqrt2)``;
* the generator's electrical volts map to a physical unit (amps) via a fixed
  calibration ``volts_to_amps`` — mirroring a real current probe / divider;
* the DUT trips a relay when a measured quantity crosses a trigger, after a
  fixed detection latency, and latches it for a fixed hold time; the scope sees
  that relay line as a rising edge followed by a falling edge.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class ChannelState:
    enabled: bool = False
    waveform: str = "DC"  # "SIN" or "DC"
    amplitude_vpp: float = 0.0
    offset_v: float = 0.0
    frequency_hz: float = 0.0
    # harmonic order -> peak-to-peak volts, superimposed on the fundamental
    harmonics: dict[int, float] = field(default_factory=dict)

    def rms_v(self) -> float:
        """RMS of this channel's AC content (fundamental + harmonics)."""
        if not self.enabled or self.waveform != "SIN":
            return 0.0
        fund = self.amplitude_vpp / (2.0 * math.sqrt(2.0))
        harm = math.sqrt(
            sum((vpp / (2.0 * math.sqrt(2.0))) ** 2 for vpp in self.harmonics.values())
        )
        return math.hypot(fund, harm)

    def harmonic_rms_v(self) -> float:
        """RMS of the harmonic content only (excludes the fundamental)."""
        if not self.enabled or self.waveform != "SIN":
            return 0.0
        return math.sqrt(
            sum((vpp / (2.0 * math.sqrt(2.0))) ** 2 for vpp in self.harmonics.values())
        )

    def dc_v(self) -> float:
        if not self.enabled:
            return 0.0
        return self.offset_v if self.waveform == "SIN" else self.amplitude_vpp


@dataclass
class SimConfig:
    """Ground-truth behaviour of the simulated DUT (from bench_config.yaml)."""

    volts_to_amps: float = 10.0
    voltage_channel: int = 1
    current_channel: int = 2
    # overcurrent
    overcurrent_trigger_a: float = 200.0
    overcurrent_detect_ms: float = 1200.0
    # harmonic (RMS of harmonic band)
    harmonic_trigger_a: float = 1.5
    harmonic_detect_ms: float = 1130.0
    # how long each relay stays latched once tripped (the ">= 3 s hold" check)
    overcurrent_hold_s: float = 3.08
    harmonic_hold_s: float = 3.20
    # analog-output correctness: DUT echoes its input with this gain error
    analog_output_gain_error: float = 0.03
    # asserted level of the DUT relay line as the scope sees it
    relay_high_v: float = 3.3

    @classmethod
    def from_mapping(cls, raw: dict[str, object] | None) -> SimConfig:
        """Build from the ``simulation:`` block of a bench config.

        Lives here rather than in the Layer 2 loader so that the standalone
        bench emulator can construct the identical device behaviour from the
        identical YAML without importing anything from Layer 2.
        """
        raw = raw or {}
        fields = {f: cls.__dataclass_fields__[f] for f in cls.__dataclass_fields__}
        unknown = set(raw) - set(fields)
        if unknown:
            raise ValueError(
                f"bench_config simulation: unknown key(s) {sorted(unknown)}; "
                f"known keys are {sorted(fields)}"
            )
        kwargs: dict[str, object] = {}
        for name, field_def in fields.items():
            if name not in raw:
                continue
            caster = int if field_def.type in ("int", int) else float
            kwargs[name] = caster(raw[name])  # type: ignore[arg-type]
        return cls(**kwargs)  # type: ignore[arg-type]


class SimulatedBench:
    """Shared, deterministic model of the bench and DUT."""

    RELAYS = ("overcurrent", "harmonic")

    def __init__(self, cfg: SimConfig, n_channels: int = 2) -> None:
        self.cfg = cfg
        self.channels: dict[int, ChannelState] = {
            ch: ChannelState() for ch in range(1, n_channels + 1)
        }
        # calibration correction applied to the DUT analog output (1.0 = none);
        # the analog-out test discovers the gain error and writes a correction.
        self.analog_correction = 1.0

    # --- generator control (shared by every transport) --------------------
    def channel(self, channel: int) -> ChannelState:
        if channel not in self.channels:
            raise KeyError(f"no such channel: {channel}")
        return self.channels[channel]

    def configure_sine(
        self, channel: int, amplitude_vpp: float, frequency_hz: float, offset_v: float = 0.0
    ) -> None:
        ch = self.channel(channel)
        ch.waveform = "SIN"
        ch.amplitude_vpp = amplitude_vpp
        ch.frequency_hz = frequency_hz
        ch.offset_v = offset_v
        ch.harmonics = {}

    def configure_dc(self, channel: int, level_v: float) -> None:
        ch = self.channel(channel)
        ch.waveform = "DC"
        ch.amplitude_vpp = level_v
        ch.harmonics = {}

    def add_harmonic(self, channel: int, order: int, amplitude_vpp: float) -> None:
        self.channel(channel).harmonics[order] = amplitude_vpp

    def clear_harmonics(self, channel: int) -> None:
        self.channel(channel).harmonics = {}

    def set_output(self, channel: int, enabled: bool) -> None:
        self.channel(channel).enabled = enabled

    def output_state(self, channel: int) -> bool:
        return self.channel(channel).enabled

    def reset(self) -> None:
        for ch in self.channels.values():
            ch.enabled = False
            ch.waveform = "DC"
            ch.amplitude_vpp = 0.0
            ch.offset_v = 0.0
            ch.frequency_hz = 0.0
            ch.harmonics = {}
        self.analog_correction = 1.0

    # --- derived physical quantities ------------------------------------
    def current_rms_a(self) -> float:
        ch = self.channels[self.cfg.current_channel]
        return ch.rms_v() * self.cfg.volts_to_amps

    def harmonic_current_rms_a(self) -> float:
        ch = self.channels[self.cfg.current_channel]
        return ch.harmonic_rms_v() * self.cfg.volts_to_amps

    def voltage_dc_v(self) -> float:
        return self.channels[self.cfg.voltage_channel].dc_v()

    # --- DUT relay logic -------------------------------------------------
    def overcurrent_tripped(self) -> bool:
        return self.current_rms_a() >= self.cfg.overcurrent_trigger_a

    def harmonic_tripped(self) -> bool:
        return self.harmonic_current_rms_a() >= self.cfg.harmonic_trigger_a

    def relay_states(self) -> dict[str, bool]:
        return {
            "overcurrent": self.overcurrent_tripped(),
            "harmonic": self.harmonic_tripped(),
        }

    def detection_time_ms(self, relay: str) -> float:
        """Deterministic detection latency for the scope to 'see'."""
        return {
            "overcurrent": self.cfg.overcurrent_detect_ms,
            "harmonic": self.cfg.harmonic_detect_ms,
        }[relay]

    def hold_time_s(self, relay: str) -> float:
        """How long the relay stays latched once tripped (the >= 3 s hold).

        Layer 2 never calls this: it measures the hold off the captured trace
        instead, the same way a bench engineer measures it on a scope. It is
        kept here because it is what ``relay_trace`` renders.
        """
        return {
            "overcurrent": self.cfg.overcurrent_hold_s,
            "harmonic": self.cfg.harmonic_hold_s,
        }[relay]

    # --- what the oscilloscope sees --------------------------------------
    def relay_trace(
        self, relay: str, gate_ms: float, sample_rate_hz: float
    ) -> tuple[float, list[float]]:
        """Render the relay line as ``(dt_s, samples)``.

        0 V until the DUT trips at ``detection_time_ms``, then ``relay_high_v``
        for ``hold_time_s``, then back to 0 V. If the relay is not tripped the
        trace is flat — the keyword layer then reports an infinite edge time,
        which is what a real "never detected" run looks like.

        Both the in-process scope driver and the networked SCPI emulator call
        this, so a trace fetched over a socket is sample-for-sample identical
        to one obtained in-process.
        """
        if relay not in self.RELAYS:
            raise KeyError(f"no such relay: {relay}")
        if sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be > 0")
        dt = 1.0 / sample_rate_hz
        n = max(1, int((gate_ms / 1000.0) / dt))
        if not self.relay_states().get(relay, False):
            return dt, [0.0] * n
        rise = int((self.detection_time_ms(relay) / 1000.0) / dt)
        fall = rise + int(self.hold_time_s(relay) / dt)
        high = self.cfg.relay_high_v
        return dt, [high if rise <= i < fall else 0.0 for i in range(n)]

    # --- analog output ---------------------------------------------------
    def analog_output_a(self, channel: int, requested_a: float) -> float:
        """DUT reproduces ``requested_a`` with a gain error, minus any
        correction the test has calibrated in."""
        raw = requested_a * (1.0 + self.cfg.analog_output_gain_error)
        return raw * self.analog_correction

    def read_analog_output(self, channel: int) -> float:
        """What the DUT reports on its analog output right now."""
        return self.analog_output_a(channel, self.current_rms_a())

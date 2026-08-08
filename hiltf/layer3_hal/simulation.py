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
* the generator's electrical volts map to physical units (amps on the current
  input, kilovolts on the line input) via fixed calibrations — mirroring a real
  current probe and voltage divider;
* **line detection is hysteretic.** The device latches "line present" when the
  voltage enters an inner window and only drops out when it leaves a wider
  outer one, so a sagging supply does not chatter the relay. The two windows
  are the non-permanent voltage limits every railway supply standard states.
* **the two overcurrent detectors work on different quantities.** The slow one
  integrates and compares an *RMS* current, which is why its detection time is
  specified as a minimum — it must ride through transients rather than trip on
  them. The fast one compares the *instantaneous* current, so when it is
  exercised with an AC stimulus the measured time necessarily includes the
  phase delay from a zero crossing up to the threshold. That is a property of
  the stimulus, not of the device, and the model reproduces it.
* **on a DC line, any AC content is contamination.** That is what the harmonic
  detector watches: the AC RMS of the current input while the line input is DC.
* **a detection produces two edges, not one.** The device asserts a digital pin
  when it decides, and the physical relay contact follows one contact-transit
  later. Detection time, relay set time and total time are three different
  numbers, and a bench measures them with two probes on one acquisition.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

SQRT2 = math.sqrt(2.0)


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
        fund = self.amplitude_vpp / (2.0 * SQRT2)
        harm = math.sqrt(sum((vpp / (2.0 * SQRT2)) ** 2 for vpp in self.harmonics.values()))
        return math.hypot(fund, harm)

    def peak_v(self) -> float:
        """Instantaneous peak of the AC content — what a fast detector sees.

        Harmonics are summed on amplitude rather than in quadrature: in the
        worst case their crests line up with the fundamental's, and a threshold
        detector is a worst-case device.
        """
        if not self.enabled or self.waveform != "SIN":
            return 0.0
        return (self.amplitude_vpp + sum(self.harmonics.values())) / 2.0

    def dc_v(self) -> float:
        if not self.enabled:
            return 0.0
        return self.offset_v if self.waveform == "SIN" else self.amplitude_vpp

    def is_ac(self) -> bool:
        return self.enabled and self.waveform == "SIN" and self.rms_v() > 0.0


@dataclass
class SimConfig:
    """Ground-truth behaviour of the simulated DUT (from bench_config.yaml)."""

    # --- bench calibrations (what the probes and dividers do) -------------
    volts_to_amps: float = 10.0  # amps per generator Vrms, current input
    volts_to_kilovolts: float = 5.0  # line kV per generator Vrms, voltage input
    voltage_channel: int = 1
    current_channel: int = 2

    # --- line detection: inner window latches in, outer window holds in ---
    # Non-permanent limits of a 1.5 kV DC supply, plus the device's drop-out
    # hysteresis. Entering requires [in_low, in_high]; staying in only requires
    # the wider [out_low, out_high].
    line_in_low_kv: float = 1.000
    line_in_high_kv: float = 1.900
    line_out_low_kv: float = 0.950
    line_out_high_kv: float = 1.950
    line_in_ms: float = 1070.0  # voltage present -> device declares line
    line_out_ms: float = 524.0  # voltage gone -> device drops line
    #: a dropout shorter than this is ridden through without dropping out
    line_hole_ride_ms: float = 400.0

    # --- overcurrent: slow integrates RMS, fast compares instantaneous ----
    overcurrent_trigger_a: float = 200.0  # RMS
    #: The slow detector must *not* trip before this. It is a floor, not a
    #: ceiling: a protection that fires early on inrush is a broken protection.
    overcurrent_detect_ms: float = 1215.0
    fast_overcurrent_trigger_a: float = 500.0  # instantaneous
    #: The fast detector's own latency, measured from the instant the current
    #: actually crosses the threshold — not from when the stimulus started.
    fast_overcurrent_detect_ms: float = 0.9

    # --- harmonic: AC content riding on a DC line ------------------------
    harmonic_trigger_a: float = 1.5  # RMS of the AC content
    harmonic_detect_ms: float = 1175.0

    # --- the contact itself ----------------------------------------------
    #: digital pin asserted -> relay contact actually moved
    relay_set_ms: float = 2.0
    #: asserted level of the DUT relay line as the scope sees it
    relay_high_v: float = 3.3

    # --- how long each relay stays latched once tripped ------------------
    overcurrent_hold_s: float = 3.08
    fast_overcurrent_hold_s: float = 3.05
    harmonic_hold_s: float = 3.20

    # --- analog outputs: 0..full-scale line volts -> a mA current loop ----
    analog_full_scale_kv: float = 3.0
    analog_full_scale_ma: float = 20.0
    #: Live zero. A loop that reads 4 mA at zero can tell "nothing to report"
    #: apart from "the wire is broken"; one that reads 0 mA cannot.
    analog_zero_ma: float = 4.0
    #: fractional gain error of each analog output, discovered by the test
    analog_error_ch1: float = 0.0009
    analog_error_ch2: float = -0.0040
    analog_error_ch3: float = -0.0060
    analog_error_ch4: float = 0.0013

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> SimConfig:
        """Build from the ``simulation:`` block of a bench config.

        Lives here rather than in the Layer 2 loader so that the standalone
        bench emulator can construct the identical device behaviour from the
        identical YAML without importing anything from Layer 2.

        The values are typed ``Any`` because that is what they are: whatever
        PyYAML decided a scalar was. Each one is coerced to the type its field
        declares, so a config written ``50000`` and one written ``5.0e4`` end
        up at the same place.
        """
        values = dict(raw or {})
        fields = cls.__dataclass_fields__
        unknown = set(values) - set(fields)
        if unknown:
            raise ValueError(
                f"bench_config simulation: unknown key(s) {sorted(unknown)}; "
                f"known keys are {sorted(fields)}"
            )
        kwargs: dict[str, Any] = {}
        for name, field_def in fields.items():
            if name not in values:
                continue
            caster: Callable[[Any], Any] = int if field_def.type in ("int", int) else float
            kwargs[name] = caster(values[name])
        return cls(**kwargs)

    def analog_error(self, channel: int) -> float:
        errors = (
            self.analog_error_ch1,
            self.analog_error_ch2,
            self.analog_error_ch3,
            self.analog_error_ch4,
        )
        if not 1 <= channel <= len(errors):
            raise KeyError(f"no such analog output channel: {channel}")
        return errors[channel - 1]


class SimulatedBench:
    """Shared, deterministic model of the bench and DUT."""

    #: Detectors the device exposes. Each one drives a digital pin and, one
    #: contact transit later, a physical relay.
    DETECTORS = ("line", "overcurrent", "fast_overcurrent", "harmonic")
    #: Backwards-compatible alias — every detector owns a relay.
    RELAYS = DETECTORS
    #: The drop-out capture. A release is triggered on the supply *going away*,
    #: so it is a different acquisition from the pick-up one, not the same
    #: trace read later. Naming it makes the trace a pure function of the
    #: device's specification instead of something the bench has to remember
    #: having been in — which would make it depend on how often it was asked.
    RELEASE = "line_release"

    def __init__(self, cfg: SimConfig, n_channels: int = 2) -> None:
        self.cfg = cfg
        self.channels: dict[int, ChannelState] = {
            ch: ChannelState() for ch in range(1, n_channels + 1)
        }
        #: Latched state of the hysteretic line detector. Everything else is a
        #: pure function of the channels; this one has memory *because the
        #: device does* — that is what hysteresis means.
        self._line_latched = False
        #: Set when an interruption outlasted the ride-through: the device has
        #: dropped out even though the supply is back, and stays out until the
        #: stimulus is applied afresh.
        self._hole_dropped = False
        # calibration correction applied to the DUT analog outputs (1.0 = none)
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
        self._hole_dropped = False

    def configure_dc(self, channel: int, level_v: float) -> None:
        ch = self.channel(channel)
        ch.waveform = "DC"
        ch.amplitude_vpp = level_v
        ch.harmonics = {}
        self._hole_dropped = False

    def add_harmonic(self, channel: int, order: int, amplitude_vpp: float) -> None:
        self.channel(channel).harmonics[order] = amplitude_vpp

    def clear_harmonics(self, channel: int) -> None:
        self.channel(channel).harmonics = {}

    def set_output(self, channel: int, enabled: bool) -> None:
        self.channel(channel).enabled = enabled
        # Switching an output is a fresh application of the supply, so whatever
        # a previous interruption did to the latch no longer applies.
        self._hole_dropped = False

    def interrupt_output(self, channel: int, duration_ms: float) -> None:
        """Drop this channel's output for a stated time, then restore it.

        A supply hole is a *timed* event, and its length is the whole point:
        the device is specified to ride through a short one and to drop out of
        a long one. Modelling it as ``output_off()`` followed by
        ``output_on()`` would throw away the duration — the only parameter that
        decides the outcome — so the interruption is a single bench action that
        carries its own length, which is also how it is produced in practice
        (a gated output, or a contactor with a timer).
        """
        if duration_ms < 0:
            raise ValueError("interruption duration must not be negative")
        self.channel(channel)  # validate before doing anything
        if duration_ms > self.cfg.line_hole_ride_ms:
            # The supply comes back, so the voltage alone would say "present"
            # again. It is the *device* that has dropped out and now has to
            # pick up from scratch, which takes its full pick-up time. With no
            # clock in this model, that is recorded as a state rather than
            # inferred from a voltage that has already recovered.
            self._hole_dropped = True

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
        self._line_latched = False
        self._hole_dropped = False
        self.analog_correction = 1.0

    # --- derived physical quantities ------------------------------------
    def current_rms_a(self) -> float:
        return self.channels[self.cfg.current_channel].rms_v() * self.cfg.volts_to_amps

    def current_peak_a(self) -> float:
        """Instantaneous peak of the injected current."""
        return self.channels[self.cfg.current_channel].peak_v() * self.cfg.volts_to_amps

    def harmonic_current_rms_a(self) -> float:
        """AC content of the current input — but only while the line is DC.

        On an AC line the fundamental *is* the line current and carries no
        information about distortion. On a DC line there should be no AC at
        all, so every ampere of it is contamination. Reporting the same number
        in both cases would be the measurement equivalent of a category error.
        """
        if self.line_is_ac():
            return 0.0
        return self.channels[self.cfg.current_channel].rms_v() * self.cfg.volts_to_amps

    def line_is_ac(self) -> bool:
        return self.channels[self.cfg.voltage_channel].is_ac()

    def voltage_dc_v(self) -> float:
        return self.channels[self.cfg.voltage_channel].dc_v()

    def line_kv(self) -> float:
        """Line voltage in kV, however the line happens to be energised."""
        ch = self.channels[self.cfg.voltage_channel]
        volts = ch.rms_v() if ch.is_ac() else ch.dc_v()
        return volts * self.cfg.volts_to_kilovolts

    # --- DUT detector logic ----------------------------------------------
    def _settle_line(self) -> None:
        """Advance the hysteretic latch to whatever the present voltage implies.

        Driven from *reads*, not from writes. The channels can be mutated
        through several doors — the in-process driver calls ``configure_dc``,
        while the SCPI emulator sets a function and an amplitude as two
        separate commands — and a latch updated on the way in would be updated
        on some of those paths and not others. The device's state would then
        depend on which transport had been used to reach it, which is precisely
        the property this repository claims it does not have.
        """
        kv = self.line_kv()
        c = self.cfg
        was = self._line_latched
        if self._hole_dropped:
            latched = False
        elif was:
            latched = c.line_out_low_kv <= kv <= c.line_out_high_kv
        else:
            latched = c.line_in_low_kv <= kv <= c.line_in_high_kv
        self._line_latched = latched

    def line_detected(self) -> bool:
        self._settle_line()
        return self._line_latched

    def ride_through(self, dropout_ms: float) -> bool:
        """Would a dropout of this length leave the line detection latched?

        The device is specified to ignore a supply hole shorter than a stated
        duration. Nothing about that is deducible from the instantaneous
        voltage, so it is asked as a question rather than inferred.
        """
        return dropout_ms <= self.cfg.line_hole_ride_ms

    def overcurrent_tripped(self) -> bool:
        return self.current_rms_a() >= self.cfg.overcurrent_trigger_a

    def fast_overcurrent_tripped(self) -> bool:
        return self.current_peak_a() >= self.cfg.fast_overcurrent_trigger_a

    def harmonic_tripped(self) -> bool:
        return self.harmonic_current_rms_a() >= self.cfg.harmonic_trigger_a

    def relay_states(self) -> dict[str, bool]:
        return {
            "line": self.line_detected(),
            "overcurrent": self.overcurrent_tripped(),
            "fast_overcurrent": self.fast_overcurrent_tripped(),
            "harmonic": self.harmonic_tripped(),
        }

    # --- timing ----------------------------------------------------------
    def _ac_phase_delay_ms(self, threshold_a: float) -> float:
        """How long a sine started at a zero crossing takes to reach a level.

        A threshold detector fed a sine cannot respond before the waveform
        physically arrives at the threshold. For a peak well above it that is
        almost immediate; just above it, it approaches a full quarter cycle.
        This is why an instantaneous-trip specification is honestly verified
        with a DC or square stimulus, and why an AC measurement of it always
        reads high — the delay belongs to the stimulus, not the device.
        """
        ch = self.channels[self.cfg.current_channel]
        peak = self.current_peak_a()
        freq = ch.frequency_hz
        if peak <= 0.0 or freq <= 0.0 or threshold_a <= 0.0:
            return 0.0
        if threshold_a >= peak:
            return 250.0 / freq  # a quarter cycle: it only just gets there
        return math.asin(threshold_a / peak) / (2.0 * math.pi * freq) * 1000.0

    def detection_time_ms(self, detector: str) -> float:
        """Stimulus applied -> the device asserts its digital pin."""
        c = self.cfg
        if detector == "line":
            return c.line_in_ms
        if detector == "overcurrent":
            return c.overcurrent_detect_ms
        if detector == "harmonic":
            return c.harmonic_detect_ms
        if detector == "fast_overcurrent":
            return (
                self._ac_phase_delay_ms(c.fast_overcurrent_trigger_a) + c.fast_overcurrent_detect_ms
            )
        raise KeyError(f"no such detector: {detector}")

    def relay_set_time_ms(self) -> float:
        """Digital pin asserted -> the physical contact has moved."""
        return self.cfg.relay_set_ms

    def release_time_ms(self, detector: str) -> float:
        """Stimulus removed -> the device de-asserts (line detection only)."""
        if detector != "line":
            raise KeyError(f"{detector} latches for a fixed hold, it has no release time")
        return self.cfg.line_out_ms

    def hold_time_s(self, detector: str) -> float:
        """How long a detector's relay stays latched once tripped.

        Layer 2 never calls this: it measures the hold off the captured trace
        instead, the same way a bench engineer measures it on a scope. It is
        kept here because it is what the trace rendering needs.

        Line detection is not latched — it follows the line — so it has no
        hold, and asking for one is a programming error rather than a
        measurement of zero.
        """
        c = self.cfg
        holds = {
            "overcurrent": c.overcurrent_hold_s,
            "fast_overcurrent": c.fast_overcurrent_hold_s,
            "harmonic": c.harmonic_hold_s,
        }
        if detector not in holds:
            raise KeyError(f"{detector} has no latched hold")
        return holds[detector]

    # --- what the oscilloscope sees --------------------------------------
    def line_names(self) -> tuple[str, ...]:
        """Every trace name the scope can be asked for."""
        names = ["stimulus", f"{self.RELEASE}_pin", self.RELEASE]
        for det in self.DETECTORS:
            names += [f"{det}_pin", det]
        return tuple(names)

    def line_trace(
        self, line: str, gate_ms: float, sample_rate_hz: float
    ) -> tuple[float, list[float]]:
        """Render one scope trace as ``(dt_s, samples)``, starting at t=0.

        t=0 is the instant the stimulus is applied, which is what a bench sets
        up with a single rising-edge trigger on the stimulus probe. Three kinds
        of trace are available, and one acquisition carries all of them:

        ``stimulus``
            the analog signal being injected — evidence, and the time origin.
        ``<detector>_pin``
            the device's digital output: asserted at the detection time.
        ``<detector>``
            the physical relay contact: asserted one contact transit later.

        Detection time is the pin edge, relay set time is the gap between the
        pin and contact edges, and total time is the contact edge. Measuring
        all three needs both digital traces, which is exactly why a real bench
        puts two probes on the device.

        Both the in-process scope driver and the networked SCPI emulator call
        this, so a trace fetched over a socket is sample-for-sample identical
        to one obtained in-process.
        """
        if sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be > 0")
        dt = 1.0 / sample_rate_hz
        n = max(1, int((gate_ms / 1000.0) / dt))

        if line == "stimulus":
            return dt, self._stimulus_samples(n, dt)

        if line in (self.RELEASE, f"{self.RELEASE}_pin"):
            delay = 0.0 if line.endswith("_pin") else self.relay_set_time_ms()
            return dt, self._release_samples(n, dt, delay)

        detector, _, suffix = line.rpartition("_")
        if suffix == "pin" and detector in self.DETECTORS:
            return dt, self._digital_samples(detector, n, dt, delay_ms=0.0)
        if line in self.DETECTORS:
            return dt, self._digital_samples(line, n, dt, delay_ms=self.relay_set_time_ms())
        raise KeyError(f"no such scope line: {line}")

    #: Kept so the SCPI verb and older callers keep working; every relay is a
    #: line, so this is a strictly narrower spelling of the same request.
    def relay_trace(
        self, relay: str, gate_ms: float, sample_rate_hz: float
    ) -> tuple[float, list[float]]:
        return self.line_trace(relay, gate_ms, sample_rate_hz)

    def _stimulus_samples(self, n: int, dt: float) -> list[float]:
        """The injected current as the scope's probe A would see it."""
        ch = self.channels[self.cfg.current_channel]
        if not ch.enabled:
            return [0.0] * n
        if ch.waveform != "SIN":
            return [ch.amplitude_vpp] * n
        peak = ch.amplitude_vpp / 2.0
        w = 2.0 * math.pi * ch.frequency_hz
        harmonics = sorted(ch.harmonics.items())
        out = []
        for i in range(n):
            t = i * dt
            v = peak * math.sin(w * t) + ch.offset_v
            for order, vpp in harmonics:
                v += (vpp / 2.0) * math.sin(w * order * t)
            out.append(v)
        return out

    def _release_samples(self, n: int, dt: float, delay_ms: float) -> list[float]:
        """A drop-out capture: asserted at t=0, opening at the release time.

        Triggered on the supply going away, so the contact is still closed when
        the sweep starts and the only edge on the trace is the one being timed.
        """
        high = self.cfg.relay_high_v
        fall = int(((self.cfg.line_out_ms + delay_ms) / 1000.0) / dt)
        return [high if i < fall else 0.0 for i in range(n)]

    def _digital_samples(self, detector: str, n: int, dt: float, delay_ms: float) -> list[float]:
        """A digital line: low, asserted at its edge, low again at release.

        If the detector is not tripped the trace is flat — the keyword layer
        then reports an infinite edge time, which is what a real "never
        detected" run looks like, and is a measurement rather than an error.
        """
        high = self.cfg.relay_high_v

        if not self.relay_states().get(detector, False):
            return [0.0] * n

        rise = int(((self.detection_time_ms(detector) + delay_ms) / 1000.0) / dt)
        if detector == "line":
            # Not latched: it stays asserted for as long as the line is there,
            # which for the duration of one capture means "to the end".
            fall = n
        else:
            fall = rise + int(self.hold_time_s(detector) / dt)
        return [high if rise <= i < fall else 0.0 for i in range(n)]

    # --- analog outputs ---------------------------------------------------
    def analog_output_ma(self, channel: int) -> float:
        """The DUT's analog output for the present line voltage, in mA.

        The outputs are current loops: zero line volts sits at the channel's
        live-zero and full scale sits at the full-scale current. They carry the
        *measured line voltage*, so the test drives a known voltage and reads
        back a current — which is why the check is an accuracy figure and not
        an equality.
        """
        c = self.cfg
        span = c.analog_full_scale_ma - c.analog_zero_ma
        # The gain error scales the *signal*, not the live zero: the 4 mA floor
        # is a reference the output stage sits on, not part of the measurement.
        # Scaling both would make the reported error grow as the signal shrinks,
        # which is the behaviour of an offset error, not a gain one.
        signal = (self.line_kv() / c.analog_full_scale_kv) * span
        return (
            c.analog_zero_ma + signal * (1.0 + c.analog_error(channel))
        ) * self.analog_correction

    def read_analog_output(self, channel: int) -> float:
        """What the DUT reports on its analog output right now, in mA."""
        return self.analog_output_ma(channel)

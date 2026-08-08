"""Layer 2 — the keyword library (the 'brain').

These functions are the reusable test steps. They speak only the Layer 3
Protocol vocabulary — never IPs, SCPI strings, VISA resource names or byte
offsets — and publish every result to the event bus for the recorder.

Two properties are load-bearing, and both are pinned by tests:

**Nothing here touches the simulation.** Every number comes back from a
Protocol method, so the identical code runs over the in-process bench, a raw
TCP socket, PyVISA and the containerised emulator. The moment a keyword reads
``controller.bench`` it silently becomes simulation-only, and the "same suites
over four transports" claim quietly stops being true.

**The generator's volts-to-amps ratio is measured, never assumed.** It would be
easy to read it out of the config — it is right there. But on a physical bench
that number is a property of the probe, the shunt and the cabling on the day,
and a stale one silently invalidates a whole campaign: every level is off by
the same factor, so the results still look plausible. So each case probes with
a small stimulus, measures what the bench actually produced, and derives the
drive from that. (Wiring facts — which channel is the current input — stay in
config; they describe how the bench is cabled, not what it measured.)
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..layer3_hal import DeviceUnderTest, Oscilloscope, Waveform
from .event_bus import EventBus
from .instrument_controller import InstrumentController
from .plan import Scenario, hold_ok, timing_ok, tolerance_window, within_tolerance
from .report_recorder import RESULT_TOPIC

#: a trace must swing at least this much to count as carrying an edge
_MIN_EDGE_SWING_V = 0.5
#: extra capture time beyond (detection limit + hold requirement)
_GATE_MARGIN_MS = 500.0
#: peak-to-peak volts used for the calibration probe — small, safe, well under
#: any scenario's trigger level
_CALIBRATION_PROBE_VPP = 1.0
_CALIBRATION_FREQ_HZ = 50.0
#: DC volts used to probe the line path. Deliberately far below any pick-up
#: threshold: a calibration probe that trips the device under test is not a
#: calibration probe, it is the first half of an untracked test.
_CALIBRATION_LINE_PROBE_V = 0.1
#: the frequency every current stimulus is injected at
_LINE_FREQ_HZ = 50.0

#: How far outside a tolerance window to sit when bracketing a trigger.
#:
#: The window is where the spec permits the trip point to be, so a level *just*
#: outside it proves nothing: generator resolution and reading noise are both
#: bigger than "just". These put the stimulus unambiguously on one side.
_BELOW_WINDOW_FACTOR = 0.95
_ABOVE_WINDOW_FACTOR = 1.05

#: Resolution of a threshold sweep, as a count of steps across the span. More
#: steps means a finer answer and a longer test; this is the knob that trades
#: them off, and it is stated once instead of hidden at each call site.
_THRESHOLD_SWEEP_STEPS = 200
#: When a plan states no detection ceiling, allow this multiple of the floor
#: before giving up, so a device that trips far too late is measured, not missed.
_NO_CEILING_HEADROOM = 2.0
#: Decimal places a commanded level survives on the wire. Every transport here
#: formats setpoints as text with this resolution, so Layer 2 rounds to it and
#: the instrument is told exactly the number the test decided on.
_COMMAND_DECIMALS = 6
#: Decimal places a *reading* is reported to.
#:
#: Every transport carries readings back as text with more precision than this,
#: so rounding to it makes two transports that agree physically also agree in
#: print. Without it, a value that lands exactly on a formatting midpoint —
#: 460.75 A shown to one decimal — can round one way in-process and the other
#: over a socket, off a difference of 10^-7. The reports would then differ in a
#: digit that means nothing, and the only real claim this repository makes
#: would appear to have broken.
_READING_DECIMALS = 3

#: How far above the spec threshold to drive when the intent is "make it trip".
#:
#: Commanding *exactly* the threshold is undefined, and this framework learned
#: it the hard way. In-process, the drive level is a Python float and the
#: arithmetic happened to land a hair above 200.000 A, so the relay tripped and
#: the test passed. Over a socket the same level goes out as SCPI text with
#: finite resolution, comes back a hair *below*, and the relay correctly did
#: not trip — the transport did not break the test, it revealed that the test
#: had been relying on the last bit of a float.
#:
#: Real benches are worse: generators have command resolution, shunts drift,
#: and detectors have hysteresis around their trip point. So a test that means
#: "cross the threshold" states its margin instead of hoping. 1 % sits well
#: inside every plan's tolerance window, so the metered value is still checked
#: against the spec value, not against the overdriven one.
TRIGGER_OVERDRIVE = 1.01


class CalibrationError(RuntimeError):
    """The bench did not respond to the calibration probe."""


# --- edge finding ---------------------------------------------------------
def auto_threshold_v(wf: Waveform) -> float | None:
    """Half-way between the trace's low and high level, or ``None`` if flat.

    Derived from the trace rather than hardcoded, so a 3.3 V logic relay, a 5 V
    one and a 24 V industrial one all work with no reconfiguration. A flat
    trace legitimately has no threshold — that is a relay that never moved, and
    returning ``None`` says so instead of inventing an edge.
    """
    if not wf.samples:
        return None
    lo, hi = min(wf.samples), max(wf.samples)
    if hi - lo < _MIN_EDGE_SWING_V:
        return None
    return (lo + hi) / 2.0


def edges_ms(wf: Waveform, threshold_v: float | None = None) -> tuple[float, float]:
    """``(rising_ms, falling_ms)`` of the first pulse; ``inf`` where absent.

    The falling edge is searched for only *after* the rising one, so a trace
    that starts high (a relay still latched from the previous scenario) cannot
    be misread as a pulse that ended immediately.
    """
    threshold = auto_threshold_v(wf) if threshold_v is None else threshold_v
    if threshold is None:
        return float("inf"), float("inf")

    rise_idx = next((i for i, v in enumerate(wf.samples) if v >= threshold), None)
    if rise_idx is None:
        return float("inf"), float("inf")

    fall_idx = next(
        (i for i in range(rise_idx, len(wf.samples)) if wf.samples[i] < threshold), None
    )
    to_ms = wf.dt_s * 1000.0
    return rise_idx * to_ms, (float("inf") if fall_idx is None else fall_idx * to_ms)


def rising_edge_ms(wf: Waveform, threshold_v: float | None = None) -> float:
    """First-crossing time of a rising edge, in milliseconds (inf if none)."""
    return edges_ms(wf, threshold_v)[0]


def falling_edge_ms(wf: Waveform, threshold_v: float | None = None) -> float:
    """When a line that starts asserted lets go, in milliseconds.

    Release is measured on a capture triggered by the stimulus *going away*, so
    the trace starts high and the interesting edge is the only one on it.
    ``edges_ms`` would happily call sample zero the rising edge and report a
    release of "the whole pulse width", which is a true statement about a
    different measurement.
    """
    threshold = auto_threshold_v(wf) if threshold_v is None else threshold_v
    if threshold is None or not wf.samples or wf.samples[0] < threshold:
        return float("inf")
    idx = next((i for i, v in enumerate(wf.samples) if v < threshold), None)
    return float("inf") if idx is None else idx * wf.dt_s * 1000.0


def pulse_width_ms(wf: Waveform, threshold_v: float | None = None) -> float:
    """How long the line stayed asserted — the relay's latch/hold duration.

    Measured from the same single acquisition as the detection time. Asking the
    device how long it holds would just read back its own intention; this
    measures what the contact actually did.
    """
    rise, fall = edges_ms(wf, threshold_v)
    if rise == float("inf"):
        return 0.0
    if fall == float("inf"):
        # Still asserted when the capture window closed. Report what was seen
        # rather than infinity — a hold longer than the gate still passes a
        # "at least N seconds" check, and the gate is chosen to allow that.
        return wf.duration_s * 1000.0 - rise
    return fall - rise


def capture_gate_ms(sc: Scenario) -> float:
    """Capture window that fits detection *and* the whole hold, plus margin.

    Deliberately wider than the limit being checked. A window sized to the
    spec cannot tell "trips late" apart from "never trips": both come back as
    a flat trace, and only one of them is a device you can ship after tuning.
    """
    latest = (
        sc.detect_max_ms
        if math.isfinite(sc.detect_max_ms)
        else max(sc.detect_min_ms * _NO_CEILING_HEADROOM, _GATE_MARGIN_MS)
    )
    return latest + sc.hold_min_s * 1000.0 + _GATE_MARGIN_MS


# --- calibration ----------------------------------------------------------
def calibrate_volts_to_amps(controller: InstrumentController, channel: int | None = None) -> float:
    """Measure amps-per-generator-Vrms on the current channel, live.

    Drive a small known sine, read the meter, divide. Everything downstream
    scales from the number this returns.
    """
    ch = controller.config.sim.current_channel if channel is None else channel
    sg, dmm = controller.signal_generator, controller.multimeter

    sg.configure_sine(ch, _CALIBRATION_PROBE_VPP, _CALIBRATION_FREQ_HZ)
    sg.output_on(ch)
    try:
        amps = dmm.measure_ac_current_rms()
    finally:
        sg.output_off(ch)

    v_rms = _CALIBRATION_PROBE_VPP / (2.0 * math.sqrt(2.0))
    if amps <= 0.0:
        raise CalibrationError(
            f"calibration probe of {_CALIBRATION_PROBE_VPP:g} Vpp on channel {ch} "
            f"produced {amps:g} A — check the current path is connected"
        )
    return amps / v_rms


def vpp_for_current(amps_rms: float, volts_to_amps: float) -> float:
    """Peak-to-peak generator volts needed to produce a target current RMS."""
    if volts_to_amps <= 0:
        raise CalibrationError(f"non-physical volts_to_amps ratio: {volts_to_amps!r}")
    return (amps_rms / volts_to_amps) * 2.0 * math.sqrt(2.0)


def quantize_level(value: float) -> float:
    """Round a drive level to the finest step the bench can actually be told.

    A generator is commanded over a wire, and every transport in this repo
    carries the level as text with finite resolution. Rounding here means the
    number Layer 2 reasons about is the number the instrument receives — over
    any of them — rather than one that survives in-process and gets truncated
    on the way out.

    That is also what makes results comparable across transports at all. An
    unrounded level is a *different* level on each one, by less than a
    microvolt; harmless until a case multiplies it by root two and reports it
    to a tenth of an amp, at which point two transports disagree in print and
    nobody can tell whether the bench or the arithmetic moved.
    """
    return round(value, _COMMAND_DECIMALS)


def vpp_for_peak_current(amps_peak: float, volts_to_amps: float) -> float:
    """Peak-to-peak volts for a target *instantaneous* current.

    A detector that compares the instantaneous value has to be driven in those
    terms. Asking for an RMS level and hoping is off by root two, which is a
    41 % error in the quantity actually being compared.
    """
    if volts_to_amps <= 0:
        raise CalibrationError(f"non-physical volts_to_amps ratio: {volts_to_amps!r}")
    return (amps_peak / volts_to_amps) * 2.0


def calibrate_volts_to_kilovolts(controller: InstrumentController) -> float:
    """Measure line-kilovolts-per-generator-volt on the voltage path, live.

    The same argument as the current path: the bench does not apply line
    voltage directly, it drives a sensor or divider whose ratio is a property
    of that hardware today. Reading it from a file would shift every threshold
    in the campaign by one common factor — the kind of error that leaves every
    number plausible and every number wrong.
    """
    ch = controller.config.sim.voltage_channel
    sg, dmm = controller.signal_generator, controller.multimeter

    sg.configure_dc(ch, _CALIBRATION_LINE_PROBE_V)
    sg.output_on(ch)
    try:
        kv = dmm.measure_line_kv()
    finally:
        sg.output_off(ch)

    if kv <= 0.0:
        raise CalibrationError(
            f"line probe of {_CALIBRATION_LINE_PROBE_V:g} V on channel {ch} produced "
            f"{kv:g} kV — check the voltage path is connected"
        )
    return kv / _CALIBRATION_LINE_PROBE_V


def volts_for_kilovolts(kv: float, kv_ratio: float) -> float:
    """Generator volts needed to present a target line voltage."""
    if kv_ratio <= 0:
        raise CalibrationError(f"non-physical volts_to_kilovolts ratio: {kv_ratio!r}")
    return quantize_level(kv / kv_ratio)


# --- reporting helpers ----------------------------------------------------
def _publish(
    bus: EventBus, suite: str, case: str, check: str, expected: str, measured: str, outcome: str
) -> None:
    bus.publish(
        RESULT_TOPIC,
        suite=suite,
        case=case,
        check=check,
        expected=expected,
        measured=measured,
        outcome=outcome,
    )


def _outcome(ok: bool, borderline: bool = False) -> str:
    if ok:
        return "PASS"
    return "CHECK" if borderline else "FAIL"


def _publish_calibration(bus: EventBus, suite: str, case: str, ratio: float) -> str:
    """Record the measured ratio as evidence — it explains every later level."""
    _publish(
        bus,
        suite,
        case,
        "Volts-to-amps ratio (measured)",
        "> 0 A/Vrms",
        f"{ratio:.4f} A/Vrms",
        "PASS",
    )
    return f"{ratio:.4f}"


# --- timing measured across two probes -----------------------------------
@dataclass(frozen=True)
class DetectorTiming:
    """The three intervals one detection actually has.

    ``detection`` is the device making up its mind — stimulus to digital pin.
    ``relay_set`` is the contact catching up — digital pin to contact.
    ``total`` is what the outside world experiences — stimulus to contact.

    They are three different numbers against three different limits, and the
    reason a bench puts two probes on the device is that no single trace
    separates them. Reporting the total as though it were the detection time
    is how a device that misses its contact-transit limit still passes: the
    slack in one budget hides the overrun in the other.
    """

    detection_ms: float
    relay_set_ms: float
    total_ms: float
    hold_s: float


def measure_detector(osc: Oscilloscope, detector: str, gate_ms: float) -> DetectorTiming:
    """Read both digital lines of one acquisition and derive all three times.

    Two reads, one acquisition — the same thing a scope does when it is asked
    for channel 2 and then channel 3 of the capture it already took. Both
    traces share a time origin, so subtracting their edges is meaningful.
    """
    pin = osc.capture_relay(f"{detector}_pin", gate_ms)
    contact = osc.capture_relay(detector, gate_ms)
    t_pin, t_contact = rising_edge_ms(pin), rising_edge_ms(contact)
    set_ms = (
        t_contact - t_pin if math.isfinite(t_pin) and math.isfinite(t_contact) else float("inf")
    )
    return DetectorTiming(t_pin, set_ms, t_contact, pulse_width_ms(contact) / 1000.0)


def _ms(value: float) -> str:
    return "no edge" if not math.isfinite(value) else f"{value:.1f} ms"


def _publish_timing(
    bus: EventBus, suite: str, case: str, sc: Scenario, timing: DetectorTiming
) -> None:
    """Record detection, contact transit, total and hold as separate results."""
    _publish(
        bus,
        suite,
        case,
        "Detection time (stimulus -> digital pin)",
        sc.detect_window,
        _ms(timing.detection_ms),
        _outcome(timing_ok(timing.detection_ms, sc.detect_min_ms, sc.detect_max_ms)),
    )
    _publish(
        bus,
        suite,
        case,
        "Relay set time (digital pin -> contact)",
        f"< {sc.relay_set_max_ms:g} ms",
        _ms(timing.relay_set_ms),
        _outcome(timing_ok(timing.relay_set_ms, max_ms=sc.relay_set_max_ms)),
    )
    # Total is the sum of two intervals that were each checked on their own, so
    # it is evidence rather than a fifth opinion — it is the number an operator
    # would quote, and it is what a single-probe measurement would have
    # returned had anyone believed that was the detection time.
    _publish(
        bus,
        suite,
        case,
        "Total time (stimulus -> contact)",
        "detection + relay set",
        _ms(timing.total_ms),
        _outcome(math.isfinite(timing.total_ms)),
    )
    _publish(
        bus,
        suite,
        case,
        "Relay hold (latched past the stimulus)",
        f">= {sc.hold_min_s:g} s",
        f"{timing.hold_s:.2f} s",
        _outcome(hold_ok(timing.hold_s, sc.hold_min_s)),
    )


# --- stimulus helpers -----------------------------------------------------
def _trigger_quantity(sc: Scenario) -> str:
    """Which property of the current a detector actually compares.

    A slow, integrating protection compares an RMS value. A fast one compares
    the instantaneous value, and the difference is not academic: the same sine
    that reads 300 A RMS peaks at 424 A, so a plan that states one and a device
    that watches the other disagree by a factor of root two.
    """
    value = (sc.extra.get("trigger_quantity") or "rms").strip().lower()
    if value not in {"rms", "peak"}:
        raise ValueError(f"{sc.name}: trigger_quantity must be 'rms' or 'peak', got {value!r}")
    return value


def _drive_vpp(quantity: str, amps: float, ratio: float) -> float:
    vpp = vpp_for_current(amps, ratio) if quantity == "rms" else vpp_for_peak_current(amps, ratio)
    return quantize_level(vpp)


def _as_quantity(quantity: str, rms_reading: float) -> float:
    """Express a meter's RMS reading in whichever quantity the spec names.

    The meter reads RMS because that is what a meter does. The conversion to a
    peak is exact only because the stimulus is a sine this framework commanded
    itself — an assumption worth stating out loud rather than burying in a
    constant. The result is rounded to the reported resolution because deriving
    a value does not create precision in it.
    """
    value = rms_reading if quantity == "rms" else rms_reading * math.sqrt(2.0)
    return round(value, _READING_DECIMALS)


def _energise_line(controller: InstrumentController, kv: float, kv_ratio: float) -> float:
    """Bring the line input up to a stated kV and report what was applied."""
    sg, dmm = controller.signal_generator, controller.multimeter
    ch = controller.config.sim.voltage_channel
    sg.configure_dc(ch, volts_for_kilovolts(kv, kv_ratio))
    sg.output_on(ch)
    return round(dmm.measure_line_kv(), _READING_DECIMALS)


# --- overcurrent (slow and fast are the same procedure) -------------------
def run_overcurrent_case(controller: InstrumentController, sc: Scenario, bus: EventBus) -> None:
    """Verify one overcurrent detector: it stays quiet below, trips above, in time.

    The trigger tolerance is verified by *bracketing* it. Driving the nominal
    trigger and checking the meter reads the nominal trigger measures the
    generator, not the device — it would pass against a DUT with no detector in
    it at all. What the specification actually claims is that the trip point
    lies inside a window, and the only way to see that is to sit below the
    window and get silence, then sit above it and get a trip.
    """
    detector = (sc.extra.get("detector") or "overcurrent").strip()
    suite = "Overcurrent Detection"
    case = sc.name
    sg, dmm, osc, dut = (
        controller.signal_generator,
        controller.multimeter,
        controller.oscilloscope,
        controller.dut,
    )
    v_ch = controller.config.sim.voltage_channel
    i_ch = controller.config.sim.current_channel
    quantity = _trigger_quantity(sc)

    kv_ratio = calibrate_volts_to_kilovolts(controller)
    ratio = calibrate_volts_to_amps(controller)
    _publish_calibration(bus, suite, case, ratio)

    # A detector on a dead line has nothing to protect: energise first, or the
    # "no trip" result below would be true for entirely the wrong reason.
    applied_kv = _energise_line(controller, float(sc.extra.get("line_kv") or 1.5), kv_ratio)

    lo, hi = tolerance_window(sc.trigger, sc.tolerance_pct)

    # 1) below the window: the relay must not move
    below = lo * _BELOW_WINDOW_FACTOR
    sg.configure_sine(i_ch, _drive_vpp(quantity, below, ratio), _LINE_FREQ_HZ)
    sg.output_on(i_ch)
    seen_below = _as_quantity(quantity, dmm.measure_ac_current_rms())
    quiet = not dut.get_relay_states()[detector]
    _publish(
        bus,
        suite,
        case,
        "No trip below the trigger window",
        f"idle at {below:.1f} A ({quantity}); window opens at {lo:.1f} A",
        f"{seen_below:.1f} A, relay {'idle' if quiet else 'driven'}",
        _outcome(quiet),
    )

    # 2) above the window: it must trip, which pins the trip point inside it
    above = hi * _ABOVE_WINDOW_FACTOR
    sg.configure_sine(i_ch, _drive_vpp(quantity, above, ratio), _LINE_FREQ_HZ)
    seen_above = _as_quantity(quantity, dmm.measure_ac_current_rms())
    tripped = dut.get_relay_states()[detector]
    _publish(
        bus,
        suite,
        case,
        "Trip above the trigger window",
        f"trip at {above:.1f} A ({quantity}); window closes at {hi:.1f} A",
        f"{seen_above:.1f} A, relay {'driven' if tripped else 'idle'}",
        _outcome(tripped),
    )

    # 3) time it well past the threshold. Right at the trip point the timing is
    #    dominated by how close the stimulus sits to it, which measures the
    #    generator's resolution rather than the device's latency.
    drive_a = float(sc.extra.get("drive_a") or above)
    sg.configure_sine(i_ch, _drive_vpp(quantity, drive_a, ratio), _LINE_FREQ_HZ)
    _publish(
        bus,
        suite,
        case,
        "Stimulus for the timing measurement",
        f"{drive_a:g} A ({quantity})",
        f"{_as_quantity(quantity, dmm.measure_ac_current_rms()):.1f} A "
        f"on a {applied_kv:.3f} kV line",
        "PASS",
    )
    _publish_timing(bus, suite, case, sc, measure_detector(osc, detector, capture_gate_ms(sc)))

    sg.output_off(i_ch)
    sg.output_off(v_ch)


# --- harmonic: AC content riding on a DC line ----------------------------
def run_harmonic_case(controller: InstrumentController, sc: Scenario, bus: EventBus) -> None:
    """Verify the harmonic detector: AC on a DC line, below then above.

    The distortion this watches for is not a high-order component of an AC
    line — it is alternating current where there should be none at all. So the
    line is held at DC and the stimulus is a plain tone on the current input,
    which is exactly what makes it contamination and exactly what the detector
    integrates.
    """
    suite, case = "Harmonic Detection", sc.name
    sg, dmm, osc, dut = (
        controller.signal_generator,
        controller.multimeter,
        controller.oscilloscope,
        controller.dut,
    )
    v_ch = controller.config.sim.voltage_channel
    i_ch = controller.config.sim.current_channel
    tone_hz = float(sc.extra.get("harmonic_hz") or _LINE_FREQ_HZ)

    kv_ratio = calibrate_volts_to_kilovolts(controller)
    ratio = calibrate_volts_to_amps(controller)
    _publish_calibration(bus, suite, case, ratio)

    applied_kv = _energise_line(controller, float(sc.extra.get("line_kv") or 1.5), kv_ratio)
    _publish(
        bus,
        suite,
        case,
        "Line held at DC",
        "a DC line, so that any AC content is contamination",
        f"{applied_kv:.3f} kV DC",
        _outcome(applied_kv > 0.0),
    )

    lo, hi = tolerance_window(sc.trigger, sc.tolerance_pct)

    below = lo * _BELOW_WINDOW_FACTOR
    sg.configure_sine(i_ch, quantize_level(vpp_for_current(below, ratio)), tone_hz)
    sg.output_on(i_ch)
    quiet = not dut.get_relay_states()["harmonic"]
    seen_below = round(dmm.measure_harmonic_current_rms(), _READING_DECIMALS)
    _publish(
        bus,
        suite,
        case,
        "No trip below the trigger window",
        f"idle at {below:.2f} A; window opens at {lo:.2f} A",
        f"{seen_below:.2f} A, relay {'idle' if quiet else 'driven'}",
        _outcome(quiet),
    )

    above = hi * _ABOVE_WINDOW_FACTOR
    sg.configure_sine(i_ch, quantize_level(vpp_for_current(above, ratio)), tone_hz)
    measured = round(dmm.measure_harmonic_current_rms(), _READING_DECIMALS)
    tripped = dut.get_relay_states()["harmonic"]
    _publish(
        bus,
        suite,
        case,
        f"Trip above the trigger window ({tone_hz:g} Hz)",
        f"trip at {above:.2f} A; window closes at {hi:.2f} A",
        f"{measured:.2f} A, relay {'driven' if tripped else 'idle'}",
        _outcome(tripped),
    )

    drive_a = float(sc.extra.get("drive_a") or above)
    sg.configure_sine(i_ch, quantize_level(vpp_for_current(drive_a, ratio)), tone_hz)
    _publish_timing(bus, suite, case, sc, measure_detector(osc, "harmonic", capture_gate_ms(sc)))

    sg.output_off(i_ch)
    sg.output_off(v_ch)


# --- line detection: thresholds, hysteresis, in/out timing, ride-through --
def run_line_detection_case(controller: InstrumentController, sc: Scenario, bus: EventBus) -> None:
    """Verify the device knows when it is connected to a live line.

    Four separate claims, and each needs its own stimulus shape:

    * the pick-up threshold is where the specification says it is;
    * the drop-out threshold is *lower* than the pick-up one — without that
      hysteresis a line sitting near the limit chatters the relay;
    * a supply hole shorter than the stated ride-through does not drop it;
    * pick-up and drop-out both happen within their time limits.
    """
    suite, case = "Line Detection", sc.name
    sg, dmm, osc, dut = (
        controller.signal_generator,
        controller.multimeter,
        controller.oscilloscope,
        controller.dut,
    )
    v_ch = controller.config.sim.voltage_channel

    kv_ratio = calibrate_volts_to_kilovolts(controller)
    _publish(
        bus,
        suite,
        case,
        "Volts-to-kilovolts ratio (measured)",
        "> 0 kV/V",
        f"{kv_ratio:.4f} kV/V",
        "PASS",
    )

    nominal_kv = sc.trigger
    in_low = float(sc.extra["in_low_kv"])
    out_low = float(sc.extra["out_low_kv"])
    tol = sc.tolerance_pct

    # 1) dead line: nothing declared
    sg.configure_dc(v_ch, volts_for_kilovolts(in_low * 0.5, kv_ratio))
    sg.output_on(v_ch)
    _publish(
        bus,
        suite,
        case,
        "No line declared well below pick-up",
        f"idle at {in_low * 0.5:.3f} kV",
        f"{round(dmm.measure_line_kv(), _READING_DECIMALS):.3f} kV, {_state(dut, 'line')}",
        _outcome(not dut.get_relay_states()["line"]),
    )

    # 2) creep up until it declares — the measured pick-up threshold
    pickup = _sweep_threshold(controller, kv_ratio, in_low * 0.5, in_low * 1.3, +1, want=True)
    _publish(
        bus,
        suite,
        case,
        "Pick-up threshold",
        f"{in_low:g} kV +/- {tol:g} %",
        f"{pickup:.3f} kV" if math.isfinite(pickup) else "never declared",
        _outcome(within_tolerance(pickup, in_low, tol)),
    )

    # 3) hysteresis: sit between drop-out and pick-up. A device without it
    #    releases here, which on a sagging supply means a chattering contact.
    between = (out_low + in_low) / 2.0
    sg.configure_dc(v_ch, volts_for_kilovolts(between, kv_ratio))
    held = dut.get_relay_states()["line"]
    _publish(
        bus,
        suite,
        case,
        "Holds below pick-up (hysteresis)",
        f"still declared at {between:.3f} kV, between drop-out and pick-up",
        f"{round(dmm.measure_line_kv(), _READING_DECIMALS):.3f} kV, {_state(dut, 'line')}",
        _outcome(held),
    )

    # 4) creep down until it releases — the measured drop-out threshold
    dropout = _sweep_threshold(controller, kv_ratio, between, out_low * 0.7, -1, want=False)
    _publish(
        bus,
        suite,
        case,
        "Drop-out threshold",
        f"{out_low:g} kV +/- {tol:g} %, below pick-up",
        f"{dropout:.3f} kV" if math.isfinite(dropout) else "never released",
        _outcome(within_tolerance(dropout, out_low, tol) and dropout < pickup),
    )

    # 5) pick-up timing, measured on both probes from a clean application
    sg.output_off(v_ch)
    applied_kv = _energise_line(controller, nominal_kv, kv_ratio)
    timing = measure_detector(osc, "line", capture_gate_ms(sc))
    _publish(
        bus,
        suite,
        case,
        "Line-in time (stimulus -> contact)",
        sc.detect_window,
        _ms(timing.total_ms),
        _outcome(timing_ok(timing.detection_ms, sc.detect_min_ms, sc.detect_max_ms)),
    )
    _publish(
        bus,
        suite,
        case,
        "Relay set time (digital pin -> contact)",
        f"< {sc.relay_set_max_ms:g} ms",
        _ms(timing.relay_set_ms),
        _outcome(timing_ok(timing.relay_set_ms, max_ms=sc.relay_set_max_ms)),
    )

    # 6) ride-through: a hole shorter than the specified one changes nothing
    hole_ms = float(sc.extra.get("hole_ms") or 300.0)
    sg.interrupt_output(v_ch, hole_ms)
    survived = dut.get_relay_states()["line"]
    _publish(
        bus,
        suite,
        case,
        "Rides through a short supply hole",
        f"still declared after a {hole_ms:g} ms interruption",
        f"{applied_kv:.3f} kV line, {_state(dut, 'line')}",
        _outcome(survived),
    )

    # 7) drop-out timing, on the falling edge this time
    sg.output_off(v_ch)
    release_max_ms = float(sc.extra.get("release_max_ms") or 1000.0)
    contact = osc.capture_relay("line_release", gate_ms=release_max_ms * 2.0)
    released_ms = falling_edge_ms(contact)
    _publish(
        bus,
        suite,
        case,
        "Line-out time (stimulus removed -> contact opens)",
        f"< {release_max_ms:g} ms",
        _ms(released_ms),
        _outcome(timing_ok(released_ms, max_ms=release_max_ms)),
    )


def _state(dut: DeviceUnderTest, relay: str) -> str:
    return "relay driven" if dut.get_relay_states()[relay] else "relay idle"


def _sweep_threshold(
    controller: InstrumentController,
    kv_ratio: float,
    start_kv: float,
    stop_kv: float,
    direction: int,
    want: bool,
) -> float:
    """Walk the line voltage until the device changes its mind, and report where.

    This is the bench procedure written down: raise the supply slowly and note
    the value at which the device reacts. The step size is what decides the
    resolution of the answer, so it is a stated fraction of the span rather
    than a number that happens to be small.
    """
    sg, dmm, dut = (
        controller.signal_generator,
        controller.multimeter,
        controller.dut,
    )
    ch = controller.config.sim.voltage_channel
    step = abs(stop_kv - start_kv) / _THRESHOLD_SWEEP_STEPS
    if step <= 0.0:
        raise ValueError("threshold sweep needs a non-empty voltage span")

    kv = start_kv
    for _ in range(_THRESHOLD_SWEEP_STEPS + 1):
        sg.configure_dc(ch, volts_for_kilovolts(kv, kv_ratio))
        if dut.get_relay_states()["line"] is want:
            return round(dmm.measure_line_kv(), _READING_DECIMALS)
        kv += direction * step
    return float("nan")


# --- analog-output correctness -------------------------------------------
def run_analog_output_case(controller: InstrumentController, sc: Scenario, bus: EventBus) -> None:
    """Check an analog output against the line voltage it claims to represent.

    The outputs are current loops, so the reading is in milliamps and the only
    way to judge it is to convert back through the loop's own scaling and
    compare kilovolts with kilovolts. A single point cannot separate a gain
    error from an offset error, so the plan carries several and each is judged
    on its own — an averaged "accuracy" would let a channel that is badly wrong
    at one end of its span hide behind being right at the other.
    """
    suite, case = "Analog Output Correctness", sc.name
    sg, dmm, dut = controller.signal_generator, controller.multimeter, controller.dut
    v_ch = controller.config.sim.voltage_channel
    channel = int(float(sc.extra.get("dut_channel") or 1))

    full_scale_kv = float(sc.extra["full_scale_kv"])
    full_scale_ma = float(sc.extra["full_scale_ma"])
    zero_ma = float(sc.extra.get("zero_ma") or 0.0)
    span_ma = full_scale_ma - zero_ma
    if span_ma <= 0.0:
        raise ValueError(f"{sc.name}: analog output span must be positive")

    points = [float(p) for p in (sc.extra.get("points_kv") or "").split(";") if p.strip()]
    if not points:
        raise ValueError(f"{sc.name}: analog output plan needs at least one points_kv entry")

    kv_ratio = calibrate_volts_to_kilovolts(controller)
    _publish(
        bus,
        suite,
        case,
        "Volts-to-kilovolts ratio (measured)",
        "> 0 kV/V",
        f"{kv_ratio:.4f} kV/V",
        "PASS",
    )

    for kv in points:
        applied = _energise_line(controller, kv, kv_ratio)
        out_ma = round(dut.read_analog_output(channel), _READING_DECIMALS)
        # Invert the loop with the *datasheet's* scaling, not the bench's: the
        # question is whether the output means what the device claims it means.
        equivalent_kv = (out_ma - zero_ma) / span_ma * full_scale_kv
        error_pct = abs(equivalent_kv - applied) / applied * 100.0 if applied else float("inf")
        _publish(
            bus,
            suite,
            case,
            f"CH{channel} at {kv:g} kV",
            f"within {sc.tolerance_pct:g} % of the applied line voltage",
            f"{out_ma:.3f} mA -> {equivalent_kv:.3f} kV vs {applied:.3f} kV "
            f"({error_pct:.3f} % error)",
            _outcome(error_pct <= sc.tolerance_pct),
        )

    sg.output_off(v_ch)
    dmm.measure_line_kv()

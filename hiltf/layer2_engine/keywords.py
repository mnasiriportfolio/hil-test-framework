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

from ..layer3_hal import Waveform
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
    """Capture window that fits detection *and* the whole hold, plus margin."""
    return sc.detect_max_ms + sc.hold_min_s * 1000.0 + _GATE_MARGIN_MS


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


# --- reporting helpers ----------------------------------------------------
def _publish(bus: EventBus, suite: str, case: str, check: str,
             expected: str, measured: str, outcome: str) -> None:
    bus.publish(RESULT_TOPIC, suite=suite, case=case, check=check,
                expected=expected, measured=measured, outcome=outcome)


def _outcome(ok: bool, borderline: bool = False) -> str:
    if ok:
        return "PASS"
    return "CHECK" if borderline else "FAIL"


def _publish_calibration(bus: EventBus, suite: str, case: str, ratio: float) -> str:
    """Record the measured ratio as evidence — it explains every later level."""
    _publish(bus, suite, case, "Volts-to-amps ratio (measured)",
             "> 0 A/Vrms", f"{ratio:.4f} A/Vrms", "PASS")
    return f"{ratio:.4f}"


# --- overcurrent ---------------------------------------------------------
def run_overcurrent_case(controller: InstrumentController, sc: Scenario, bus: EventBus) -> None:
    suite, case = "Overcurrent Detection", sc.name
    sg, dmm, osc, dut = (
        controller.signal_generator,
        controller.multimeter,
        controller.oscilloscope,
        controller.dut,
    )
    ch = controller.config.sim.current_channel

    ratio = calibrate_volts_to_amps(controller)
    _publish_calibration(bus, suite, case, ratio)

    # 1) not-driven baseline
    sg.output_off(ch)
    baseline = dmm.measure_ac_current_rms()
    idle = not dut.get_relay_states()["overcurrent"]
    _publish(bus, suite, case, "Relay idle before stimulus",
             "no trip", f"{baseline:.2f} A", _outcome(idle))

    # 2) drive just past the trigger and confirm the metered value
    sg.configure_sine(ch, vpp_for_current(sc.trigger * TRIGGER_OVERDRIVE, ratio), 50.0)
    sg.output_on(ch)
    measured = dmm.measure_ac_current_rms()
    lo, hi = tolerance_window(sc.trigger, sc.tolerance_pct)
    trig_ok = within_tolerance(measured, sc.trigger, sc.tolerance_pct)
    _publish(bus, suite, case, "Trigger level",
             f"{sc.trigger:g} A ({lo:.1f}-{hi:.1f})", f"{measured:.2f} A", _outcome(trig_ok))

    # 3) one acquisition, two measurements: detection latency and latch duration
    wf = osc.capture_relay("overcurrent", gate_ms=capture_gate_ms(sc))
    t_ms = rising_edge_ms(wf)
    _publish(bus, suite, case, "Detection time (scope)",
             f"<= {sc.detect_max_ms:g} ms", f"{t_ms:.1f} ms",
             _outcome(timing_ok(t_ms, sc.detect_max_ms)))

    hold_s = pulse_width_ms(wf) / 1000.0
    _publish(bus, suite, case, "Relay hold (scope)",
             f">= {sc.hold_min_s:g} s", f"{hold_s:.2f} s",
             _outcome(hold_ok(hold_s, sc.hold_min_s)))

    sg.output_off(ch)


# --- harmonic ------------------------------------------------------------
def run_harmonic_case(controller: InstrumentController, sc: Scenario, bus: EventBus) -> None:
    suite, case = "Harmonic Detection", sc.name
    sg, dmm, osc = controller.signal_generator, controller.multimeter, controller.oscilloscope
    v_ch = controller.config.sim.voltage_channel
    i_ch = controller.config.sim.current_channel
    order = int(float(sc.extra.get("harmonic_order", 3)))

    ratio = calibrate_volts_to_amps(controller)
    _publish_calibration(bus, suite, case, ratio)

    # hold a DC voltage 'catenary', then inject a harmonic on the current input
    sg.configure_dc(v_ch, 1500.0)
    sg.output_on(v_ch)
    sg.configure_sine(i_ch, 0.0, 50.0)
    sg.add_harmonic(i_ch, order, vpp_for_current(sc.trigger * TRIGGER_OVERDRIVE, ratio))
    sg.output_on(i_ch)

    measured = dmm.measure_harmonic_current_rms()
    lo, hi = tolerance_window(sc.trigger, sc.tolerance_pct)
    trig_ok = within_tolerance(measured, sc.trigger, sc.tolerance_pct)
    _publish(bus, suite, case, f"Harmonic trigger (order {order})",
             f"{sc.trigger:g} A ({lo:.2f}-{hi:.2f})", f"{measured:.2f} A", _outcome(trig_ok))

    wf = osc.capture_relay("harmonic", gate_ms=capture_gate_ms(sc))
    t_ms = rising_edge_ms(wf)
    _publish(bus, suite, case, "Detection time (scope)",
             f"<= {sc.detect_max_ms:g} ms", f"{t_ms:.1f} ms",
             _outcome(timing_ok(t_ms, sc.detect_max_ms)))

    hold_s = pulse_width_ms(wf) / 1000.0
    _publish(bus, suite, case, "Relay hold (scope)",
             f">= {sc.hold_min_s:g} s", f"{hold_s:.2f} s",
             _outcome(hold_ok(hold_s, sc.hold_min_s)))

    sg.output_off(i_ch)
    sg.output_off(v_ch)


# --- analog-output correctness (with self-calibration) -------------------
def run_analog_output_case(controller: InstrumentController, sc: Scenario, bus: EventBus) -> None:
    suite, case = "Analog Output Correctness", sc.name
    sg, dmm, dut = controller.signal_generator, controller.multimeter, controller.dut
    i_ch = controller.config.sim.current_channel
    channel = int(float(sc.extra.get("dut_channel", 1)))

    ratio = calibrate_volts_to_amps(controller)
    _publish_calibration(bus, suite, case, ratio)

    # drive a known input current and read the DUT's reproduced output
    sg.configure_sine(i_ch, vpp_for_current(sc.trigger, ratio), 50.0)
    sg.output_on(i_ch)

    requested = dmm.measure_ac_current_rms()
    out = dut.read_analog_output(channel)
    err_pct = abs(out - requested) / requested * 100.0
    ok = err_pct <= sc.tolerance_pct
    # out-of-tolerance here is expected and gets corrected below, so flag it as
    # CHECK (informational) rather than a hard FAIL.
    _publish(bus, suite, case, f"CH{channel} output error (pre-cal)",
             f"<= {sc.tolerance_pct:g} %", f"{err_pct:.2f} %", _outcome(ok, borderline=True))

    # if out of tolerance, self-calibrate a correction factor and re-verify
    if not ok:
        correction = requested / out
        dut.apply_analog_correction(correction)
        out2 = dut.read_analog_output(channel)
        err2 = abs(out2 - requested) / requested * 100.0
        _publish(bus, suite, case, f"CH{channel} auto-calibration",
                 "apply correction, error <= tol",
                 f"factor {correction:.4f} -> {err2:.2f} %", _outcome(err2 <= sc.tolerance_pct))
        dut.apply_analog_correction(1.0)  # reset for the next scenario

    sg.output_off(i_ch)

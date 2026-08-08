"""Layer 2 — the test engine (orchestration, plans, keywords, reporting)."""

from .config_loader import BenchConfig, ConfigError, load_config
from .event_bus import Event, EventBus
from .instrument_controller import InstrumentController
from .keywords import (
    CalibrationError,
    DetectorTiming,
    auto_threshold_v,
    calibrate_volts_to_amps,
    calibrate_volts_to_kilovolts,
    capture_gate_ms,
    edges_ms,
    falling_edge_ms,
    measure_detector,
    pulse_width_ms,
    rising_edge_ms,
    run_analog_output_case,
    run_harmonic_case,
    run_line_detection_case,
    run_overcurrent_case,
    volts_for_kilovolts,
    vpp_for_current,
    vpp_for_peak_current,
)
from .plan import (
    Scenario,
    enabled_scenarios,
    hold_ok,
    load_plan,
    timing_ok,
    tolerance_window,
    within_tolerance,
)
from .report_recorder import CaseResult, ReportRecorder, StepResult

__all__ = [
    # config
    "BenchConfig",
    "ConfigError",
    "load_config",
    # bus + reporting
    "Event",
    "EventBus",
    "ReportRecorder",
    "CaseResult",
    "StepResult",
    # orchestration
    "InstrumentController",
    # plans + spec math
    "Scenario",
    "load_plan",
    "enabled_scenarios",
    "tolerance_window",
    "within_tolerance",
    "timing_ok",
    "hold_ok",
    # keywords
    "run_line_detection_case",
    "run_overcurrent_case",
    "run_harmonic_case",
    "run_analog_output_case",
    "calibrate_volts_to_amps",
    "calibrate_volts_to_kilovolts",
    "vpp_for_current",
    "vpp_for_peak_current",
    "volts_for_kilovolts",
    "CalibrationError",
    "capture_gate_ms",
    "auto_threshold_v",
    "edges_ms",
    "rising_edge_ms",
    "falling_edge_ms",
    "pulse_width_ms",
    "measure_detector",
    "DetectorTiming",
]

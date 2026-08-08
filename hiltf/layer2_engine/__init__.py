"""Layer 2 — the test engine (orchestration, plans, keywords, reporting)."""
from .config_loader import BenchConfig, ConfigError, load_config
from .event_bus import Event, EventBus
from .instrument_controller import InstrumentController
from .keywords import (
    CalibrationError,
    auto_threshold_v,
    calibrate_volts_to_amps,
    capture_gate_ms,
    edges_ms,
    pulse_width_ms,
    rising_edge_ms,
    run_analog_output_case,
    run_harmonic_case,
    run_overcurrent_case,
    vpp_for_current,
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
    "run_overcurrent_case",
    "run_harmonic_case",
    "run_analog_output_case",
    "calibrate_volts_to_amps",
    "vpp_for_current",
    "CalibrationError",
    "capture_gate_ms",
    "auto_threshold_v",
    "edges_ms",
    "rising_edge_ms",
    "pulse_width_ms",
]

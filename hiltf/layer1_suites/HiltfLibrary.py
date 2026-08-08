"""Layer 1 — the Robot Framework keyword library.

Thin adapter that exposes the Layer 2 engine as Robot keywords. The ``.robot``
files call these by name; they contain no Python, no addresses, no channel
numbers and no idea which transport is in use — only the test intent.
"""

from __future__ import annotations

from pathlib import Path

from robot.api import logger
from robot.api.deco import keyword, library

from hiltf.layer2_engine import (
    EventBus,
    InstrumentController,
    ReportRecorder,
    enabled_scenarios,
    load_config,
    load_plan,
    run_analog_output_case,
    run_harmonic_case,
    run_line_detection_case,
    run_overcurrent_case,
)

_ROOT = Path(__file__).resolve().parents[2]
_RUNNERS = {
    "line_detection": run_line_detection_case,
    "overcurrent": run_overcurrent_case,
    "harmonic": run_harmonic_case,
    "analog_output": run_analog_output_case,
}


@library(scope="SUITE")
class HiltfLibrary:
    def __init__(self) -> None:
        self.bus = EventBus()
        self.recorder = ReportRecorder(self.bus)
        self.controller: InstrumentController | None = None

    def _require_controller(self) -> InstrumentController:
        if self.controller is None:
            raise AssertionError("call 'Open Bench' first")
        return self.controller

    @keyword
    def open_bench(self, config: str = "config/bench_config.yaml") -> None:
        """Load a bench config, build its drivers and connect them all."""
        cfg = load_config(_ROOT / config)
        self.controller = InstrumentController(cfg)
        self.controller.connect_all()
        logger.info(f"Bench '{cfg.name}' from {config}", also_console=True)
        logger.info(f"Drivers: {cfg.driver_names()}", also_console=True)

    @keyword
    def close_bench(self) -> None:
        if self.controller:
            self.controller.disconnect_all()

    @keyword
    def log_bench_identities(self) -> dict[str, str]:
        """Query every instrument's identity and put it in the log.

        Report evidence, and a fast failure when something is unreachable.
        """
        identities = self._require_controller().identify_all()
        for role, ident in identities.items():
            logger.info(f"{role:17s} {ident}", also_console=True)
        return identities

    @keyword
    def run_all_cases(self, kind: str, plan: str) -> None:
        controller = self._require_controller()
        if kind not in _RUNNERS:
            raise AssertionError(f"unknown case kind {kind!r}; known: {sorted(_RUNNERS)}")
        runner = _RUNNERS[kind]
        for sc in enabled_scenarios(load_plan(_ROOT / plan)):
            runner(controller, sc, self.bus)

    @keyword
    def all_cases_passed(self) -> None:
        recorder = self.recorder
        if not recorder.all_passed:
            failing = [c.case for c in recorder.cases if c.outcome == "FAIL"]
            raise AssertionError(f"Failing cases: {failing}")

    @keyword
    def write_report(self, path: str = "reports/report.md") -> str:
        out = self.recorder.write_markdown(_ROOT / path)
        logger.info(f"Report written to {out}", also_console=True)
        return str(out)

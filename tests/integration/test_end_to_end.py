"""End-to-end: build the simulated bench from config, run every test type
through the engine, and assert the reports come out green."""
from pathlib import Path

import pytest

from hiltf.layer2_engine import (
    EventBus,
    InstrumentController,
    ReportRecorder,
    enabled_scenarios,
    load_config,
    load_plan,
    run_analog_output_case,
    run_harmonic_case,
    run_overcurrent_case,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def rig():
    cfg = load_config(ROOT / "config" / "bench_config.yaml")
    controller = InstrumentController(cfg)
    controller.connect_all()
    bus = EventBus()
    recorder = ReportRecorder(bus)
    yield controller, bus, recorder
    controller.disconnect_all()


def test_overcurrent_end_to_end(rig):
    controller, bus, recorder = rig
    for sc in enabled_scenarios(load_plan(ROOT / "config" / "overcurrent_plan.csv")):
        run_overcurrent_case(controller, sc, bus)
    assert recorder.cases
    assert recorder.all_passed
    assert recorder.cases[0].outcome == "PASS"


def test_harmonic_end_to_end(rig):
    controller, bus, recorder = rig
    for sc in enabled_scenarios(load_plan(ROOT / "config" / "harmonic_plan.csv")):
        run_harmonic_case(controller, sc, bus)
    assert recorder.all_passed


def test_analog_output_self_calibrates(rig):
    controller, bus, recorder = rig
    for sc in enabled_scenarios(load_plan(ROOT / "config" / "analog_out_plan.csv")):
        run_analog_output_case(controller, sc, bus)
    # pre-cal is out of tolerance (CHECK) then auto-cal brings it in (no FAIL)
    assert recorder.all_passed
    checks = [s.outcome for c in recorder.cases for s in c.steps]
    assert "CHECK" in checks
    assert "FAIL" not in checks


def test_report_renders_markdown(rig):
    controller, bus, recorder = rig
    for sc in enabled_scenarios(load_plan(ROOT / "config" / "overcurrent_plan.csv")):
        run_overcurrent_case(controller, sc, bus)
    md = recorder.render_markdown()
    assert "# HIL Test Framework" in md
    assert "Overcurrent Detection" in md

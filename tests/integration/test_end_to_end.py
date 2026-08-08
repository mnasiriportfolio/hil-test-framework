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
    run_line_detection_case,
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


def test_line_detection_end_to_end(rig):
    controller, bus, recorder = rig
    for sc in enabled_scenarios(load_plan(ROOT / "config" / "line_detection_plan.csv")):
        run_line_detection_case(controller, sc, bus)
    assert recorder.all_passed
    checks = [s.check for c in recorder.cases for s in c.steps]
    # the four claims the case exists to separate
    assert any("Pick-up threshold" in c for c in checks)
    assert any("hysteresis" in c for c in checks)
    assert any("supply hole" in c for c in checks)
    assert any("Line-out time" in c for c in checks)


def test_every_detection_case_starts_by_proving_silence(rig):
    """A detector that is always on passes any test that only drives it hard.

    So each case sits below the tolerance window first and requires the relay
    to stay idle. Without that step the suite cannot tell a working detector
    from a shorted contact.
    """
    controller, bus, recorder = rig
    for plan, runner in (
        ("config/overcurrent_plan.csv", run_overcurrent_case),
        ("config/harmonic_plan.csv", run_harmonic_case),
    ):
        for sc in enabled_scenarios(load_plan(ROOT / plan)):
            runner(controller, sc, bus)

    assert recorder.cases
    for case in recorder.cases:
        checks = [s.check for s in case.steps]
        assert "No trip below the trigger window" in checks, case.case


def test_analog_output_is_checked_at_every_plan_point(rig):
    """Accuracy is judged point by point, never averaged into one verdict.

    A channel with a gain error is wrong by a consistent fraction across its
    span; a channel with an offset error is fine in one place and bad in
    another. One summary figure cannot tell those apart, so each point gets
    its own row and its own outcome.
    """
    controller, bus, recorder = rig
    scenarios = enabled_scenarios(load_plan(ROOT / "config" / "analog_out_plan.csv"))
    for sc in scenarios:
        run_analog_output_case(controller, sc, bus)

    assert recorder.all_passed
    by_name = {sc.name: sc for sc in scenarios}
    assert {c.case for c in recorder.cases} == set(by_name)
    for case in recorder.cases:
        points = [p for p in by_name[case.case].extra["points_kv"].split(";") if p.strip()]
        measured = [s for s in case.steps if s.check.endswith("kV")]
        assert len(measured) == len(points)
        assert all("mA ->" in s.measured for s in measured)


def test_report_renders_markdown(rig):
    controller, bus, recorder = rig
    for sc in enabled_scenarios(load_plan(ROOT / "config" / "overcurrent_plan.csv")):
        run_overcurrent_case(controller, sc, bus)
    md = recorder.render_markdown()
    assert "# HIL Test Framework" in md
    assert "Overcurrent Detection" in md

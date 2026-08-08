"""The claim, tested: the same suites over different transports agree.

Everything else in this repository is in service of this file. The architecture
is only worth describing if a run over raw TCP sockets and binary UDP produces
the *same report* as a run in-process — step for step, value for value.

If a transport ever starts changing a result, this fails, and the layering
claim in the README stops being decorative.
"""
from __future__ import annotations

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

PLANS = [
    ("config/overcurrent_plan.csv", run_overcurrent_case),
    ("config/harmonic_plan.csv", run_harmonic_case),
    ("config/analog_out_plan.csv", run_analog_output_case),
]


def run_everything(config) -> ReportRecorder:
    bus = EventBus()
    recorder = ReportRecorder(bus)
    with InstrumentController(config) as controller:
        for plan_path, runner in PLANS:
            for sc in enabled_scenarios(load_plan(ROOT / plan_path)):
                runner(controller, sc, bus)
    return recorder


def steps(recorder: ReportRecorder) -> list[tuple[str, ...]]:
    return [
        (c.suite, c.case, s.check, s.expected, s.measured, s.outcome)
        for c in recorder.cases
        for s in c.steps
    ]


@pytest.fixture()
def in_process(sim_bench_config):
    return run_everything(sim_bench_config)


def test_in_process_run_is_green(in_process):
    assert in_process.cases
    assert in_process.all_passed


def test_socket_run_matches_in_process_exactly(in_process, emulator, retarget):
    """Raw TCP/SCPI for the instruments, binary UDP for the DUT."""
    socket_config = retarget(load_config(ROOT / "config" / "bench_socket.yaml"), emulator)
    over_socket = run_everything(socket_config)

    assert over_socket.all_passed
    assert steps(over_socket) == steps(in_process)


def test_the_two_runs_really_used_different_drivers(sim_bench_config, emulator, retarget):
    """Guards against the parity test passing because nothing changed."""
    socket_config = retarget(load_config(ROOT / "config" / "bench_socket.yaml"), emulator)

    assert sim_bench_config.driver_names() == {
        "signal_generator": "sim_signal_generator",
        "multimeter": "sim_multimeter",
        "oscilloscope": "sim_oscilloscope",
        "dut": "sim_dut",
    }
    assert socket_config.driver_names() == {
        "signal_generator": "lan_signal_generator",
        "multimeter": "lan_multimeter",
        "oscilloscope": "lan_oscilloscope",
        "dut": "udp_dut",
    }


def test_reports_are_byte_identical(in_process, emulator, retarget):
    """Same evidence, whichever way the bench was reached.

    Only the timestamp line differs, so it is dropped before comparing.
    """
    socket_config = retarget(load_config(ROOT / "config" / "bench_socket.yaml"), emulator)
    over_socket = run_everything(socket_config)

    def body(recorder: ReportRecorder) -> list[str]:
        return [ln for ln in recorder.render_markdown().splitlines()
                if not ln.startswith("_Generated")]

    assert body(over_socket) == body(in_process)


def test_identities_show_which_bench_answered(emulator, retarget):
    socket_config = retarget(load_config(ROOT / "config" / "bench_socket.yaml"), emulator)
    with InstrumentController(socket_config) as controller:
        identities = controller.identify_all()
    assert identities["multimeter"].startswith("HILTF,BENCH-EMULATOR")
    assert identities["dut"].startswith("HILTF-SIM-DUT")

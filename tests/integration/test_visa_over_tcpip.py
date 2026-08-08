"""PyVISA, end to end, over a real socket.

``pyvisa-py`` (a pure-Python VISA implementation) pointed at a
``TCPIP0::host::port::SOCKET`` resource, talking to this repo's bench emulator.
No vendor VISA runtime, no hardware — but the entire PyVISA path is live:
resource manager, session, termination handling, IEEE 488.2 block transfer.

The last test is the one that matters: the identical scenario set, run through
PyVISA, produces the identical report as the in-process run. Swapping VISA for
sockets for a simulation is a line in a YAML file, and this is what keeps that
sentence true.
"""
from __future__ import annotations

import math
from pathlib import Path

import pytest

pytest.importorskip("pyvisa", reason="install the optional extra: pip install -e '.[visa]'")
pytest.importorskip("pyvisa_py", reason="install the optional extra: pip install -e '.[visa]'")

from hiltf.layer2_engine import (  # noqa: E402
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
from hiltf.layer3_hal import scpi_commands as scpi  # noqa: E402
from hiltf.layer3_hal.visa_drivers import (  # noqa: E402
    VisaMultimeter,
    VisaOscilloscope,
    VisaSignalGenerator,
)

ROOT = Path(__file__).resolve().parents[2]
CURRENT_CH = 2

PLANS = [
    ("config/overcurrent_plan.csv", run_overcurrent_case),
    ("config/harmonic_plan.csv", run_harmonic_case),
    ("config/analog_out_plan.csv", run_analog_output_case),
]


@pytest.fixture()
def visa_rack(emulator):
    resource = emulator.scpi_resource
    sg = VisaSignalGenerator(resource, backend="@py", timeout_s=10.0)
    dmm = VisaMultimeter(resource, backend="@py", timeout_s=10.0)
    osc = VisaOscilloscope(resource, backend="@py", timeout_s=30.0)
    for drv in (sg, dmm, osc):
        drv.connect()
    try:
        yield sg, dmm, osc
    finally:
        for drv in (sg, dmm, osc):
            drv.disconnect()


def test_identity_over_visa(visa_rack):
    sg, dmm, osc = visa_rack
    for drv in (sg, dmm, osc):
        assert drv.identify() == scpi.IDN


def test_stimulus_and_measurement_over_visa(visa_rack):
    sg, dmm, _ = visa_rack
    assert dmm.measure_ac_current_rms() == pytest.approx(0.0)

    sg.configure_sine(CURRENT_CH, 20.0 * 2.0 * math.sqrt(2.0), 50.0)
    sg.output_on(CURRENT_CH)
    assert dmm.measure_ac_current_rms() == pytest.approx(200.0, rel=1e-4)
    assert sg.output_state(CURRENT_CH) is True

    sg.output_off(CURRENT_CH)
    assert dmm.measure_ac_current_rms() == pytest.approx(0.0)


def test_binary_block_transfer_over_visa(visa_rack):
    """~1 MB of float32 through a VISA session, with the right edges.

    This is where termination handling has to be correct: the payload contains
    bytes that look like terminators, and a read that stops at one returns a
    short, plausible, wrong waveform.
    """
    sg, _, osc = visa_rack
    sg.configure_sine(CURRENT_CH, 30.0 * 2.0 * math.sqrt(2.0), 50.0)  # 300 A
    sg.output_on(CURRENT_CH)

    wf = osc.capture_relay("overcurrent", gate_ms=5500.0)

    assert wf.sample_rate_hz == pytest.approx(50_000.0)
    assert len(wf.samples) == 275_000
    assert wf.samples[int(0.5 * 50_000)] == 0.0
    assert wf.samples[int(2.0 * 50_000)] == pytest.approx(3.3, rel=1e-5)
    assert wf.samples[int(5.0 * 50_000)] == 0.0


def test_the_session_survives_a_binary_transfer(visa_rack):
    """Termination is restored afterwards, so text queries still work.

    A driver that switches termination off for the block and forgets to put it
    back leaves the session subtly broken — and the failure shows up in the
    *next* test, not this one.
    """
    sg, dmm, osc = visa_rack
    sg.configure_sine(CURRENT_CH, 30.0 * 2.0 * math.sqrt(2.0), 50.0)
    sg.output_on(CURRENT_CH)

    osc.capture_relay("overcurrent", gate_ms=500.0)
    assert osc.identify() == scpi.IDN
    assert dmm.measure_ac_current_rms() == pytest.approx(300.0, rel=1e-4)


def test_full_run_over_visa_matches_in_process(emulator, retarget, sim_bench_config):
    """The whole claim, through PyVISA."""
    def run(config) -> list[tuple[str, ...]]:
        bus = EventBus()
        recorder = ReportRecorder(bus)
        with InstrumentController(config) as controller:
            for plan_path, runner in PLANS:
                for sc in enabled_scenarios(load_plan(ROOT / plan_path)):
                    runner(controller, sc, bus)
        assert recorder.all_passed
        return [
            (c.suite, c.case, s.check, s.expected, s.measured, s.outcome)
            for c in recorder.cases
            for s in c.steps
        ]

    visa_config = retarget(load_config(ROOT / "config" / "bench_visa.yaml"), emulator)
    assert visa_config.driver_names()["multimeter"] == "visa_multimeter"

    assert run(visa_config) == run(sim_bench_config)

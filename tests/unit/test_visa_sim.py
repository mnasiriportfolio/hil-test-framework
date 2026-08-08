"""PyVISA driver contract, against the ``pyvisa-sim`` backend.

No network, no VISA runtime, no hardware — pyvisa-sim answers from
``hiltf/emulators/visa_sim.yaml``. What this proves is the part a simulated
device *can* prove: that the driver emits the header it should and parses the
reply it gets back. A wrong header, a mis-parsed float or a missing
synchronisation point all fail here, in milliseconds, on every push.

What it deliberately does not prove is behaviour: pyvisa-sim answers from a
static table and cannot know that 20 Vrms into the current channel should read
200 A. That is what the bench emulator is for — see
``tests/integration/test_visa_over_tcpip.py``, which drives the same drivers
through pyvisa-py against a live server.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pyvisa", reason="install the optional extra: pip install -e '.[visa]'")
pytest.importorskip("pyvisa_sim", reason="install the optional extra: pip install -e '.[visa]'")

from hiltf.layer3_hal import scpi_commands as scpi  # noqa: E402
from hiltf.layer3_hal.visa_drivers import (  # noqa: E402
    VisaMultimeter,
    VisaSignalGenerator,
    list_resources,
)

ROOT = Path(__file__).resolve().parents[2]
SIM_YAML = str(ROOT / "hiltf" / "emulators" / "visa_sim.yaml")
RESOURCE = "ASRL1::INSTR"


@pytest.fixture()
def dmm():
    driver = VisaMultimeter(RESOURCE, backend="@sim", sim_yaml=SIM_YAML, timeout_s=2.0)
    driver.connect()
    try:
        yield driver
    finally:
        driver.disconnect()


@pytest.fixture()
def generator():
    driver = VisaSignalGenerator(RESOURCE, backend="@sim", sim_yaml=SIM_YAML, timeout_s=2.0)
    driver.connect()
    try:
        yield driver
    finally:
        driver.disconnect()


def test_the_simulated_resource_is_discoverable():
    assert RESOURCE in list_resources("@sim", SIM_YAML)


def test_identity_is_read_through_visa(dmm):
    assert dmm.identify() == scpi.IDN


def test_scalar_queries_are_parsed(dmm):
    """Each of these fails if the driver sends the wrong header."""
    assert dmm.measure_dc_voltage() == pytest.approx(1500.0)
    assert dmm.measure_ac_current_rms() == pytest.approx(200.0)
    assert dmm.measure_harmonic_current_rms() == pytest.approx(1.5)


def test_configuration_completes_and_synchronises(generator):
    """The commands are ignored by the simulated device; ``*OPC?`` is not.

    A driver that forgot to answer-check its synchronisation point would hang
    here until the VISA timeout instead of returning.
    """
    generator.configure_sine(2, 5.0, 50.0)
    generator.configure_dc(1, 1500.0)
    generator.add_harmonic(2, 3, 0.5)
    generator.output_on(2)
    generator.output_off(2)


def test_output_state_is_parsed(generator):
    assert generator.output_state(1) is False


def test_session_lifecycle(dmm):
    assert dmm.is_connected
    assert dmm.io.is_open
    dmm.disconnect()
    assert not dmm.io.is_open
    with pytest.raises(RuntimeError, match="before connect"):
        dmm.measure_dc_voltage()


def test_backend_spec_selects_pyvisa_sim():
    from hiltf.layer3_hal.visa_drivers import VisaSession

    assert VisaSession("x", backend="@sim", sim_yaml="a.yaml")._backend_spec() == "a.yaml@sim"
    assert VisaSession("x", backend="@py")._backend_spec() == "@py"
    assert VisaSession("x", backend="")._backend_spec() == ""

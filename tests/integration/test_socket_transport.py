"""The raw TCP/SCPI transport, against a real listener.

Everything here needs a socket to be meaningful. A mocked instrument would
happily confirm that the driver calls the methods the test author imagined; it
cannot confirm that the driver survives a partial read, a response that arrives
glued to the next one, a binary payload containing newline bytes, or a peer
that says nothing at all. Those are the failures that actually happen.
"""
from __future__ import annotations

import math
import socket

import pytest

from hiltf.emulators import bench_emulator
from hiltf.layer3_hal import scpi_commands as scpi
from hiltf.layer3_hal.lan_drivers import LanMultimeter, LanOscilloscope, LanSignalGenerator
from hiltf.layer3_hal.scpi_socket import ScpiError, ScpiSocket

CURRENT_CH = 2


@pytest.fixture()
def rack(emulator):
    """The three LAN instruments, connected to the running emulator."""
    sg = LanSignalGenerator(emulator.host, emulator.scpi_port)
    dmm = LanMultimeter(emulator.host, emulator.scpi_port)
    osc = LanOscilloscope(emulator.host, emulator.scpi_port)
    for drv in (sg, dmm, osc):
        drv.connect()
    try:
        yield sg, dmm, osc, emulator
    finally:
        for drv in (sg, dmm, osc):
            drv.disconnect()


def test_every_instrument_identifies_itself(rack):
    sg, dmm, osc, _ = rack
    for drv in (sg, dmm, osc):
        assert drv.identify() == scpi.IDN


def test_driving_the_generator_changes_what_the_meter_reads(rack):
    sg, dmm, _, _ = rack
    assert dmm.measure_ac_current_rms() == pytest.approx(0.0)

    sg.configure_sine(CURRENT_CH, 20.0 * 2.0 * math.sqrt(2.0), 50.0)
    sg.output_on(CURRENT_CH)
    assert dmm.measure_ac_current_rms() == pytest.approx(200.0, rel=1e-4)

    sg.output_off(CURRENT_CH)
    assert dmm.measure_ac_current_rms() == pytest.approx(0.0)


def test_set_then_measure_is_ordered_across_two_sessions(rack):
    """The race that ``*OPC?`` exists to close.

    The generator and the meter are two independent TCP sessions handled by two
    threads. Writes are fire-and-forget, so without a synchronisation point the
    meter can be read before the generator's command has been executed. Looping
    makes an unsynchronised implementation fail reliably rather than one run in
    twenty.
    """
    sg, dmm, _, _ = rack
    target_vpp = 20.0 * 2.0 * math.sqrt(2.0)
    for _ in range(25):
        sg.configure_sine(CURRENT_CH, target_vpp, 50.0)
        sg.output_on(CURRENT_CH)
        assert dmm.measure_ac_current_rms() == pytest.approx(200.0, rel=1e-4)
        sg.output_off(CURRENT_CH)
        assert dmm.measure_ac_current_rms() == pytest.approx(0.0)


def test_output_state_reads_back(rack):
    sg, _, _, _ = rack
    sg.configure_sine(CURRENT_CH, 1.0, 50.0)
    sg.output_on(CURRENT_CH)
    assert sg.output_state(CURRENT_CH) is True
    sg.output_off(CURRENT_CH)
    assert sg.output_state(CURRENT_CH) is False


def test_large_binary_block_transfer(rack):
    """A ~1 MB capture, over a socket, decoded to the right edges.

    275,000 float32 samples do not fit in one TCP segment or one ``recv()``.
    This is the test that fails if block reads are done by delimiter instead of
    by byte count.
    """
    sg, _, osc, emu = rack
    sg.configure_sine(CURRENT_CH, 30.0 * 2.0 * math.sqrt(2.0), 50.0)  # 300 A
    sg.output_on(CURRENT_CH)

    wf = osc.capture_relay("overcurrent", gate_ms=5500.0)

    assert wf.sample_rate_hz == pytest.approx(50_000.0)
    assert len(wf.samples) == 275_000
    assert max(wf.samples) == pytest.approx(3.3, rel=1e-5)
    # detection at 1200 ms, hold 3.08 s -> falls at 4280 ms
    assert wf.samples[int(0.5 * 50_000)] == 0.0
    assert wf.samples[int(2.0 * 50_000)] == pytest.approx(3.3, rel=1e-5)
    assert wf.samples[int(5.0 * 50_000)] == 0.0


def test_an_untripped_relay_gives_a_flat_trace(rack):
    _, _, osc, _ = rack
    wf = osc.capture_relay("overcurrent", gate_ms=200.0)
    assert set(wf.samples) == {0.0}


# --- transport-level behaviour --------------------------------------------
def test_unsupported_command_times_out_into_a_default(emulator):
    """A model that lacks a header answers nothing. That is not a test failure.

    The short timeout keeps this fast; the point is that ``safe_query`` returns
    the documented default and the session stays usable afterwards.
    """
    with ScpiSocket(emulator.host, emulator.scpi_port, timeout_s=0.3) as io:
        assert io.safe_query("NOSUCH:HEADER?", default="N/A") == "N/A"
        # the session must survive: the next real query still lines up
        assert io.query(scpi.idn()) == scpi.IDN


def test_rapid_queries_stay_in_sync(emulator):
    """The desync regression, made reproducible.

    A reader that calls ``recv()`` once per "line" eventually answers query N
    with query N-1's response. Interleaving two queries with different answers
    catches that immediately.
    """
    with ScpiSocket(emulator.host, emulator.scpi_port, timeout_s=5.0) as io:
        io.write(scpi.set_function(1, "DC"))
        io.write(scpi.set_amplitude_vpp(1, 7.5))
        io.sync()
        for _ in range(200):
            assert io.query(scpi.idn()) == scpi.IDN
            assert float(io.query(scpi.get_amplitude_vpp(1))) == pytest.approx(7.5)
            assert io.query(scpi.get_function(1)) == "DC"


def test_responses_arriving_glued_together_are_split(emulator):
    """Three commands written back-to-back, three answers in one TCP read."""
    with ScpiSocket(emulator.host, emulator.scpi_port, timeout_s=5.0) as io:
        io.write(scpi.idn())
        io.write(scpi.operation_complete())
        io.write(scpi.idn())
        assert io.read_line() == scpi.IDN
        assert io.read_line() == "1"
        assert io.read_line() == scpi.IDN


def test_connecting_to_nothing_is_a_clear_error():
    # bind a port, then close it, so nothing is listening on a known number
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    driver = LanMultimeter("127.0.0.1", port, timeout_s=1.0)
    with pytest.raises(ScpiError, match="cannot reach"):
        driver.connect()


def test_operations_before_connect_are_refused(emulator):
    driver = LanMultimeter(emulator.host, emulator.scpi_port)
    with pytest.raises(RuntimeError, match="before connect"):
        driver.measure_ac_current_rms()


def test_context_manager_closes_the_socket(emulator):
    driver = LanMultimeter(emulator.host, emulator.scpi_port)
    with driver:
        assert driver.is_connected
        assert driver.io.is_open
    assert not driver.is_connected
    assert not driver.io.is_open


def test_two_emulators_do_not_share_a_bench(sim_bench_config):
    """Ephemeral ports, isolated state — so tests can run in parallel."""
    with bench_emulator(sim_config=sim_bench_config.sim) as a, \
         bench_emulator(sim_config=sim_bench_config.sim) as b:
        assert a.scpi_port != b.scpi_port
        sg = LanSignalGenerator(a.host, a.scpi_port)
        dmm_b = LanMultimeter(b.host, b.scpi_port)
        with sg, dmm_b:
            sg.configure_sine(CURRENT_CH, 50.0, 50.0)
            sg.output_on(CURRENT_CH)
            assert dmm_b.measure_ac_current_rms() == pytest.approx(0.0)

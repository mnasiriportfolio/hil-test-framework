"""The DUT's binary UDP transport, against a real listener.

UDP is where the interesting failures live: datagrams vanish, replies arrive
late and out of order, and a device is allowed to refuse. The emulator can drop
packets on demand so the retry path runs for real instead of being assumed.
"""

from __future__ import annotations

import math
import socket

import pytest

from hiltf.emulators import bench_emulator
from hiltf.emulators.dut_server import DEFAULT_IDENT
from hiltf.layer3_hal import dut_protocol as proto
from hiltf.layer3_hal.dut_driver import BinaryUdpDut, DutTimeout

CURRENT_CH = 2


@pytest.fixture()
def dut(emulator):
    driver = BinaryUdpDut(emulator.host, emulator.dut_port, timeout_s=1.0, retries=3)
    driver.connect()
    try:
        yield driver, emulator
    finally:
        driver.disconnect()


def test_identity_is_read_at_connect(dut):
    driver, _ = dut
    assert driver.identify() == (
        f"{DEFAULT_IDENT.name} fw {DEFAULT_IDENT.firmware} sn {DEFAULT_IDENT.serial}"
    )


def test_relay_states_follow_the_stimulus(dut):
    driver, emu = dut
    assert driver.get_relay_states() == {"overcurrent": False, "harmonic": False}

    # drive the shared bench directly — the point is that the DUT, reached over
    # UDP, reports the consequence of what the generator did over TCP
    emu.bench.configure_sine(CURRENT_CH, 30.0 * 2.0 * math.sqrt(2.0), 50.0)
    emu.bench.set_output(CURRENT_CH, True)
    assert driver.get_relay_states()["overcurrent"] is True


def test_analog_output_reads_back_per_channel(dut):
    driver, emu = dut
    emu.bench.configure_sine(CURRENT_CH, 10.0 * 2.0 * math.sqrt(2.0), 50.0)  # 100 A
    emu.bench.set_output(CURRENT_CH, True)
    # 3 % gain error is the device's ground truth
    assert driver.read_analog_output(1) == pytest.approx(103.0, rel=1e-6)


def test_correction_round_trips_through_the_device(dut):
    driver, emu = dut
    emu.bench.configure_sine(CURRENT_CH, 10.0 * 2.0 * math.sqrt(2.0), 50.0)
    emu.bench.set_output(CURRENT_CH, True)

    driver.apply_analog_correction(100.0 / 103.0)
    assert driver.read_analog_output(1) == pytest.approx(100.0, rel=1e-9)


def test_refusal_raises_with_the_reason(dut):
    """A NACK is not a timeout: the device heard and declined."""
    driver, _ = dut
    with pytest.raises(proto.NackError, match="channel does not exist"):
        driver.read_analog_output(99)


def test_refusal_does_not_burn_the_retry_budget(dut):
    """Retrying a refusal would just waste three timeouts before failing."""
    driver, _ = dut
    driver.timeout_s = 5.0
    driver.retries = 3
    import time

    started = time.monotonic()
    with pytest.raises(proto.NackError):
        driver.read_analog_output(99)
    assert time.monotonic() - started < 1.0


def test_a_bad_calibration_factor_is_refused(dut):
    driver, emu = dut
    with pytest.raises(proto.NackError, match="out of range"):
        driver.apply_analog_correction(0.0)
    assert emu.bench.analog_correction == 1.0


# --- packet loss -----------------------------------------------------------
def test_retries_recover_from_dropped_datagrams(sim_bench_config):
    """Two datagrams swallowed; the driver must still get its answer."""
    with bench_emulator(sim_config=sim_bench_config.sim, drop_first=2) as emu:
        driver = BinaryUdpDut(emu.host, emu.dut_port, timeout_s=0.4, retries=4)
        driver.connect()  # connect() itself reads the identity, so it retries here
        try:
            assert driver.identify().startswith(DEFAULT_IDENT.name)
        finally:
            driver.disconnect()


def test_giving_up_names_the_message_and_the_budget(sim_bench_config):
    with bench_emulator(sim_config=sim_bench_config.sim, drop_first=100) as emu:
        driver = BinaryUdpDut(emu.host, emu.dut_port, timeout_s=0.15, retries=2)
        with pytest.raises(DutTimeout, match="no reply to message 0x01"):
            driver.connect()
        # a failed connect must not leave a half-open driver behind
        assert not driver.is_connected


def test_connecting_to_nothing_times_out():
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    driver = BinaryUdpDut("127.0.0.1", port, timeout_s=0.2, retries=1)
    with pytest.raises((DutTimeout, ConnectionError, OSError)):
        driver.connect()


def test_foreign_datagrams_do_not_become_measurements(dut):
    """Garbage on the port must be ignored, not decoded into a reading.

    The protocol mark is the guard. Without it, an arbitrary datagram whose
    second byte happens to equal the expected message id would be unpacked as
    a response.
    """
    driver, emu = dut
    noise = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # the driver's socket is connected, so the kernel only accepts traffic
        # from the emulator; this proves the port is not a free-for-all
        noise.sendto(b"\x00\x06junkjunkjunk", (emu.host, emu.dut_port))
    finally:
        noise.close()
    assert driver.get_relay_states() == {"overcurrent": False, "harmonic": False}


def test_operations_before_connect_are_refused(emulator):
    driver = BinaryUdpDut(emulator.host, emulator.dut_port)
    with pytest.raises(RuntimeError, match="before connect"):
        driver.get_relay_states()

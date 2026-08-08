"""Shared fixtures.

The integration tests here need no hardware and no container: they start the
bench emulator in-process on OS-assigned ports, so ``pytest`` alone exercises
the socket and UDP transports end to end.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from hiltf.emulators import bench_emulator
from hiltf.layer2_engine import load_config

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def sim_bench_config():
    """The in-process bench config, freshly loaded."""
    return load_config(ROOT / "config" / "bench_config.yaml")


@pytest.fixture()
def emulator(sim_bench_config):
    """A running SCPI + UDP bench emulator on ephemeral ports."""
    with bench_emulator(sim_config=sim_bench_config.sim, sample_rate_hz=50_000.0) as emu:
        yield emu


def _retarget(config, emu, scpi_roles=("signal_generator", "multimeter", "oscilloscope")):
    """Point a config's instrument blocks at a running emulator's real ports.

    Configs on disk name port 5025 / 50000. Tests must never bind fixed ports —
    another process may hold them, and parallel runs would collide — so the
    emulator takes whatever the OS gives it and the config is retargeted here.
    """
    config = copy.deepcopy(config)
    for role in scpi_roles:
        spec = config.instruments[role]
        if "resource" in spec:
            spec["resource"] = f"TCPIP0::{emu.host}::{emu.scpi_port}::SOCKET"
        else:
            spec["address"] = emu.host
            spec["port"] = emu.scpi_port
    dut = config.instruments["dut"]
    dut["address"] = emu.host
    dut["port"] = emu.dut_port
    return config


@pytest.fixture()
def retarget():
    """Fixture wrapper around :func:`_retarget`."""
    return _retarget

"""Bench emulators — the instruments and the DUT, as real network services.

The project ships four transports (in-process, raw TCP/SCPI, PyVISA, binary
UDP). Three of them need something on the other end of a socket. That is this
package: one :class:`~hiltf.layer3_hal.simulation.SimulatedBench` shared by a
SCPI server and a DUT server, so a full test run happens over real sockets with
consistent physics and no hardware.

Two ways to run it:

* ``python -m hiltf.emulators`` — long-running, used by the ``bench`` service
  in ``docker-compose.yml``.
* :func:`bench_emulator` — a context manager that binds ephemeral ports and
  tears everything down, used by the integration tests.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from dataclasses import dataclass

from ..layer3_hal.simulation import SimConfig, SimulatedBench
from .dut_server import DutServer
from .scpi_server import ScpiInterpreter, ScpiServer

__all__ = [
    "BenchEmulator",
    "DutServer",
    "ScpiInterpreter",
    "ScpiServer",
    "bench_emulator",
]


@dataclass
class BenchEmulator:
    """Handles for a running emulator pair."""

    bench: SimulatedBench
    host: str
    scpi_port: int
    dut_port: int

    @property
    def scpi_resource(self) -> str:
        """The VISA resource string for this emulator's SCPI port."""
        return f"TCPIP0::{self.host}::{self.scpi_port}::SOCKET"


@contextlib.contextmanager
def bench_emulator(
    sim_config: SimConfig | None = None,
    host: str = "127.0.0.1",
    scpi_port: int = 0,
    dut_port: int = 0,
    sample_rate_hz: float = 50_000.0,
    n_channels: int = 2,
    drop_first: int = 0,
) -> Iterator[BenchEmulator]:
    """Run both emulators on one shared bench for the duration of the block.

    Ports default to 0, meaning "let the OS choose". Tests must never hardcode
    a port: a fixed port makes the suite fail when anything else on the machine
    happens to hold it, and makes parallel test runs collide.
    """
    bench = SimulatedBench(sim_config or SimConfig(), n_channels=n_channels)
    scpi = ScpiServer(bench, host=host, port=scpi_port, sample_rate_hz=sample_rate_hz)
    dut = DutServer(bench, host=host, port=dut_port, drop_first=drop_first)
    scpi.start_background()
    dut.start_background()
    try:
        yield BenchEmulator(bench=bench, host=host, scpi_port=scpi.port, dut_port=dut.port)
    finally:
        for server in (scpi, dut):
            server.shutdown()
            server.server_close()

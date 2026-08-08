"""Run the bench emulator as a long-lived service.

    python -m hiltf.emulators [--config config/bench_config.yaml]

This is the entry point for the ``bench`` service in ``docker-compose.yml``.
It binds 0.0.0.0 so the container is reachable from the ``runner`` services on
the compose network, and it loads the *same* ``simulation:`` block the tests
load, so the device the runner talks to over the network behaves identically to
the one an in-process run gets.

Ports come from the environment (``HILTF_SCPI_PORT`` / ``HILTF_DUT_PORT``) so
compose can move them without editing anything.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
from pathlib import Path
from typing import Any

import yaml

from ..layer3_hal.simulation import SimConfig, SimulatedBench
from .dut_server import DutServer
from .scpi_server import ScpiServer

LOG = logging.getLogger("hiltf.emulator")


def _load_simulation(path: Path | None) -> tuple[SimConfig, int, float]:
    """Read ``simulation:`` and a couple of bench hints out of a config file."""
    if path is None or not path.exists():
        LOG.warning("no config file at %s — using built-in defaults", path)
        return SimConfig(), 2, 50_000.0
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    instruments = data.get("instruments") or {}
    channels = int((instruments.get("signal_generator") or {}).get("channels", 2))
    sample_rate = float((instruments.get("oscilloscope") or {}).get("sample_rate_hz", 50_000.0))
    return SimConfig.from_mapping(data.get("simulation")), channels, sample_rate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the HILTF bench emulator.")
    parser.add_argument(
        "--config",
        default=os.environ.get("HILTF_BENCH_CONFIG", "config/bench_config.yaml"),
        help="bench config to take the simulation: block from",
    )
    parser.add_argument("--host", default=os.environ.get("HILTF_BIND_HOST", "0.0.0.0"))
    parser.add_argument(
        "--scpi-port", type=int, default=int(os.environ.get("HILTF_SCPI_PORT", 5025))
    )
    parser.add_argument(
        "--dut-port", type=int, default=int(os.environ.get("HILTF_DUT_PORT", 50000))
    )
    parser.add_argument("--log-level", default=os.environ.get("HILTF_LOG_LEVEL", "INFO"))
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        stream=sys.stdout,
    )

    sim, channels, sample_rate = _load_simulation(Path(args.config) if args.config else None)
    bench = SimulatedBench(sim, n_channels=channels)

    scpi = ScpiServer(bench, host=args.host, port=args.scpi_port, sample_rate_hz=sample_rate)
    dut = DutServer(bench, host=args.host, port=args.dut_port)
    scpi.start_background()
    dut.start_background()

    LOG.info("SCPI  (TCP) listening on %s:%d", args.host, scpi.port)
    LOG.info("DUT   (UDP) listening on %s:%d", args.host, dut.port)
    LOG.info("scope sample rate %.0f Hz, %d generator channel(s)", sample_rate, channels)
    LOG.info("bench emulator ready")

    stop = threading.Event()

    def _shutdown(signum: int, _frame: object) -> None:
        LOG.info("signal %s received — shutting down", signal.Signals(signum).name)
        stop.set()

    # Containers are stopped with SIGTERM; without this the process is killed
    # after the grace period instead of closing its listeners.
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    try:
        stop.wait()
    finally:
        for server in (scpi, dut):
            server.shutdown()
            server.server_close()
        LOG.info("bench emulator stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

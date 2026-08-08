"""Layer 2 — the instrument controller.

Owns the driver lifecycle for a run: build from config, connect everything,
hand the objects to the keywords, disconnect on the way out (including when a
keyword raised, which is when you most want the outputs turned off).

It does **not** decide which driver class to use — the Layer 3 registry does
that from ``bench_config.yaml``. So this file contains no instrument names, no
addresses and no ``if simulated:``; it works unchanged whether the bench is an
in-process simulation, a rack on a LAN, a PyVISA resource or a container.
"""

from __future__ import annotations

from typing import Any

from ..layer3_hal import (
    DeviceUnderTest,
    Multimeter,
    Oscilloscope,
    SignalGenerator,
    build_context,
    build_driver,
)
from .config_loader import BenchConfig

#: role name in the config -> attribute name on the controller
ROLES = {
    "signal_generator": "signal_generator",
    "multimeter": "multimeter",
    "oscilloscope": "oscilloscope",
    "dut": "dut",
}


class InstrumentController:
    def __init__(self, config: BenchConfig) -> None:
        self.config = config
        instruments = config.instruments

        # Layer 3 decides whether a simulation object is needed at all. On a
        # socket- or VISA-configured bench there is none in the process, so
        # there is nothing here for a stray reference to read a "measurement"
        # from — the numbers can only have come down the wire.
        ctx = build_context(instruments, config.sim, config.root)
        built: dict[str, Any] = {role: build_driver(role, instruments[role], ctx) for role in ROLES}

        self.signal_generator: SignalGenerator = built["signal_generator"]
        self.multimeter: Multimeter = built["multimeter"]
        self.oscilloscope: Oscilloscope = built["oscilloscope"]
        self.dut: DeviceUnderTest = built["dut"]

    @property
    def drivers(self) -> tuple[Any, ...]:
        return (self.signal_generator, self.multimeter, self.oscilloscope, self.dut)

    def connect_all(self) -> None:
        """Connect every driver, unwinding cleanly if one of them fails.

        Without the unwind, a bench where the third instrument is powered off
        leaves the first two connected and their outputs live.
        """
        connected: list[Any] = []
        try:
            for drv in self.drivers:
                drv.connect()
                connected.append(drv)
        except Exception:
            for drv in reversed(connected):
                try:
                    drv.disconnect()
                except Exception:  # noqa: BLE001 - never mask the original failure
                    pass
            raise

    def disconnect_all(self) -> None:
        """Disconnect everything; one failure must not strand the others."""
        errors: list[str] = []
        for drv in self.drivers:
            try:
                drv.disconnect()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{getattr(drv, 'name', drv)}: {exc}")
        if errors:
            raise RuntimeError("errors while disconnecting: " + "; ".join(errors))

    def identify_all(self) -> dict[str, str]:
        """``{role: identity}`` — goes at the top of a report as bench evidence."""
        return {role: getattr(self, attr).identify() for role, attr in ROLES.items()}

    def __enter__(self) -> InstrumentController:
        self.connect_all()
        return self

    def __exit__(self, *exc: object) -> None:
        self.disconnect_all()

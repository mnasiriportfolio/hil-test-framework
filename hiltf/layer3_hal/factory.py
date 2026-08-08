"""Layer 3 (HAL) — the driver registry.

``bench_config.yaml`` names a driver per role; this module turns that name into
an object. It is the single seam where "which transport" is decided, and it is
the reason Layer 2 has no ``if simulated:`` branch anywhere in it.

Adding a transport is: write a class that satisfies the Protocol, register it
here, name it in the YAML. Nothing above Layer 3 changes — which is the claim
the four shipped transports exist to keep honest.

Unknown keys are rejected rather than ignored. A misspelled ``timout_s`` that
silently keeps the default is the kind of thing that gets discovered at 2am on
a bench, so it fails at load instead.
"""
from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .dut_driver import BinaryUdpDut
from .lan_drivers import LanMultimeter, LanOscilloscope, LanSignalGenerator
from .sim_drivers import (
    SimDeviceUnderTest,
    SimMultimeter,
    SimOscilloscope,
    SimSignalGenerator,
)
from .simulation import SimulatedBench
from .visa_drivers import VisaMultimeter, VisaOscilloscope, VisaSignalGenerator

#: ``${VAR}`` or ``${VAR:-fallback}``
_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


class DriverConfigError(ValueError):
    """The bench config asked for something this registry cannot build."""


@dataclass
class DriverContext:
    """Everything a builder may need that does not come from the YAML."""

    #: shared in-process bench, for the simulated drivers
    bench: SimulatedBench | None = None
    #: repository root, for resolving relative paths in the config
    root: Path = Path(".")


def expand_env(value: Any) -> Any:
    """Expand ``${VAR}`` / ``${VAR:-default}`` inside a config string.

    This is what lets one config file serve both ``docker compose`` (where the
    emulator is the host ``bench``) and a developer's laptop (where it is
    ``127.0.0.1``) without a second file or a code branch.
    """
    if not isinstance(value, str):
        return value

    def _sub(match: re.Match[str]) -> str:
        name, default = match.group(1), match.group(2)
        found = os.environ.get(name)
        if found is not None:
            return found
        if default is not None:
            return default
        raise DriverConfigError(
            f"bench config references ${{{name}}} but it is not set, and no "
            f"default was given (write ${{{name}:-fallback}} to allow one)"
        )

    return _ENV_PATTERN.sub(_sub, value)


def _spec(raw: dict[str, Any], allowed: set[str], driver: str) -> dict[str, Any]:
    """Validate and env-expand one instrument's config block."""
    unknown = set(raw) - allowed - {"driver"}
    if unknown:
        raise DriverConfigError(
            f"driver '{driver}': unknown config key(s) {sorted(unknown)}; "
            f"allowed keys are {sorted(allowed)}"
        )
    return {k: expand_env(v) for k, v in raw.items() if k != "driver"}


def _bench(ctx: DriverContext, driver: str) -> SimulatedBench:
    if ctx.bench is None:
        raise DriverConfigError(f"driver '{driver}' needs a simulated bench, but none was created")
    return ctx.bench


def _resolve(ctx: DriverContext, path: str | None) -> str | None:
    if not path:
        return None
    candidate = Path(path)
    return str(candidate if candidate.is_absolute() else (ctx.root / candidate))


# --- builders -------------------------------------------------------------
def _sim_signal_generator(raw: dict[str, Any], ctx: DriverContext) -> Any:
    _spec(raw, {"channels", "name"}, "sim_signal_generator")
    return SimSignalGenerator(_bench(ctx, "sim_signal_generator"),
                              name=raw.get("name", "SIM-AFG"))


def _sim_multimeter(raw: dict[str, Any], ctx: DriverContext) -> Any:
    _spec(raw, {"name"}, "sim_multimeter")
    return SimMultimeter(_bench(ctx, "sim_multimeter"), name=raw.get("name", "SIM-DMM"))


def _sim_oscilloscope(raw: dict[str, Any], ctx: DriverContext) -> Any:
    spec = _spec(raw, {"sample_rate_hz", "name"}, "sim_oscilloscope")
    return SimOscilloscope(
        _bench(ctx, "sim_oscilloscope"),
        sample_rate_hz=float(spec.get("sample_rate_hz", 50_000.0)),
        name=spec.get("name", "SIM-OSC"),
    )


def _sim_dut(raw: dict[str, Any], ctx: DriverContext) -> Any:
    _spec(raw, {"name"}, "sim_dut")
    return SimDeviceUnderTest(_bench(ctx, "sim_dut"), name=raw.get("name", "SIM-DUT"))


_LAN_KEYS = {"address", "port", "timeout_s", "name"}


def _lan_signal_generator(raw: dict[str, Any], ctx: DriverContext) -> Any:
    s = _spec(raw, _LAN_KEYS, "lan_signal_generator")
    return LanSignalGenerator(
        host=str(s["address"]), port=int(s.get("port", 5025)),
        timeout_s=float(s.get("timeout_s", 10.0)), name=s.get("name", "LAN-AFG"),
    )


def _lan_multimeter(raw: dict[str, Any], ctx: DriverContext) -> Any:
    s = _spec(raw, _LAN_KEYS, "lan_multimeter")
    return LanMultimeter(
        host=str(s["address"]), port=int(s.get("port", 5025)),
        timeout_s=float(s.get("timeout_s", 10.0)), name=s.get("name", "LAN-DMM"),
    )


def _lan_oscilloscope(raw: dict[str, Any], ctx: DriverContext) -> Any:
    s = _spec(raw, _LAN_KEYS, "lan_oscilloscope")
    return LanOscilloscope(
        host=str(s["address"]), port=int(s.get("port", 5025)),
        timeout_s=float(s.get("timeout_s", 30.0)), name=s.get("name", "LAN-OSC"),
    )


_VISA_KEYS = {"resource", "visa_backend", "sim_yaml", "timeout_s", "name"}


def _visa_kwargs(s: dict[str, Any], ctx: DriverContext, default_timeout: float) -> dict[str, Any]:
    return {
        "resource": str(s["resource"]),
        "backend": str(s.get("visa_backend", "@sim")),
        "sim_yaml": _resolve(ctx, s.get("sim_yaml")),
        "timeout_s": float(s.get("timeout_s", default_timeout)),
    }


def _visa_signal_generator(raw: dict[str, Any], ctx: DriverContext) -> Any:
    s = _spec(raw, _VISA_KEYS, "visa_signal_generator")
    return VisaSignalGenerator(name=s.get("name", "VISA-AFG"), **_visa_kwargs(s, ctx, 10.0))


def _visa_multimeter(raw: dict[str, Any], ctx: DriverContext) -> Any:
    s = _spec(raw, _VISA_KEYS, "visa_multimeter")
    return VisaMultimeter(name=s.get("name", "VISA-DMM"), **_visa_kwargs(s, ctx, 10.0))


def _visa_oscilloscope(raw: dict[str, Any], ctx: DriverContext) -> Any:
    s = _spec(raw, _VISA_KEYS, "visa_oscilloscope")
    return VisaOscilloscope(name=s.get("name", "VISA-OSC"), **_visa_kwargs(s, ctx, 30.0))


def _udp_dut(raw: dict[str, Any], ctx: DriverContext) -> Any:
    s = _spec(raw, {"address", "port", "timeout_s", "retries", "name"}, "udp_dut")
    return BinaryUdpDut(
        host=str(s["address"]), port=int(s.get("port", 50000)),
        timeout_s=float(s.get("timeout_s", 2.0)), retries=int(s.get("retries", 3)),
        name=s.get("name", "UDP-DUT"),
    )


Builder = Callable[[dict[str, Any], DriverContext], Any]

REGISTRY: dict[str, Builder] = {
    # in-process simulation — zero dependencies, the default bench
    "sim_signal_generator": _sim_signal_generator,
    "sim_multimeter": _sim_multimeter,
    "sim_oscilloscope": _sim_oscilloscope,
    "sim_dut": _sim_dut,
    # raw TCP socket, SCPI, standard library only
    "lan_signal_generator": _lan_signal_generator,
    "lan_multimeter": _lan_multimeter,
    "lan_oscilloscope": _lan_oscilloscope,
    # PyVISA: pyvisa-sim, pyvisa-py, or a system VISA library
    "visa_signal_generator": _visa_signal_generator,
    "visa_multimeter": _visa_multimeter,
    "visa_oscilloscope": _visa_oscilloscope,
    # the DUT's own binary protocol over UDP
    "udp_dut": _udp_dut,
}

#: which registry entries need the shared in-process bench
SIMULATED_DRIVERS = frozenset(
    {"sim_signal_generator", "sim_multimeter", "sim_oscilloscope", "sim_dut"}
)


def build_driver(role: str, raw: dict[str, Any], ctx: DriverContext) -> Any:
    """Build the driver named by ``raw['driver']`` for ``role``."""
    if not isinstance(raw, dict):
        raise DriverConfigError(f"instruments.{role}: expected a mapping, got {type(raw).__name__}")
    name = raw.get("driver")
    if not name:
        raise DriverConfigError(f"instruments.{role}: missing required key 'driver'")
    builder = REGISTRY.get(str(name))
    if builder is None:
        raise DriverConfigError(
            f"instruments.{role}: unknown driver '{name}'. "
            f"Registered drivers: {sorted(REGISTRY)}"
        )
    try:
        return builder(raw, ctx)
    except KeyError as exc:
        raise DriverConfigError(
            f"instruments.{role} ({name}): missing required key {exc}"
        ) from exc


def needs_simulated_bench(instruments: dict[str, Any]) -> bool:
    """True if any configured role is served by an in-process simulated driver."""
    return any(
        isinstance(spec, dict) and spec.get("driver") in SIMULATED_DRIVERS
        for spec in instruments.values()
    )


def build_context(
    instruments: dict[str, Any],
    sim_config: Any,
    root: Path = Path("."),
) -> DriverContext:
    """Create the context the builders need, including the shared bench.

    The in-process bench is created **only** if some role actually asked for a
    simulated driver. That is deliberate: it means a socket- or VISA-configured
    run has no simulation object in the process at all, so there is nothing for
    a stray reference to accidentally read a "measurement" from.

    It also keeps Layer 2 from ever importing ``SimulatedBench``. Deciding
    whether a simulation exists is a Layer 3 concern.
    """
    bench = None
    if needs_simulated_bench(instruments):
        n_channels = int((instruments.get("signal_generator") or {}).get("channels", 2))
        bench = SimulatedBench(sim_config, n_channels=n_channels)
    return DriverContext(bench=bench, root=root)

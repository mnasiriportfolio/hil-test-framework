"""Layer 2 — load and validate the one bench config file.

Moving to a different bench — or from the in-process simulation to sockets, to
PyVISA, or to real instruments — is a change to a YAML file *only*. The loader
validates structure early so a typo fails loudly at load time, not deep inside
a test run three minutes later.

Note what this module does **not** do: it never inspects which transport was
chosen. It hands the ``instruments:`` block to the Layer 3 registry and takes
back objects. That is why there is no ``if simulated:`` anywhere in Layer 2.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..layer3_hal import SimConfig

_REQUIRED_TOP = ("bench", "instruments")
_REQUIRED_ROLES = ("signal_generator", "multimeter", "oscilloscope", "dut")


class ConfigError(ValueError):
    """The bench config is missing something, or says something impossible."""


@dataclass
class BenchConfig:
    name: str
    simulate: bool
    instruments: dict[str, Any]
    sim: SimConfig
    raw: dict[str, Any] = field(default_factory=dict)
    path: Path | None = None

    @property
    def root(self) -> Path:
        """Directory the config's relative paths resolve against (repo root)."""
        if self.path is None:
            return Path(".")
        # config/bench_config.yaml -> repository root
        return self.path.resolve().parent.parent

    def driver_names(self) -> dict[str, str]:
        """``{role: driver}`` — handy in logs and reports, and in tests."""
        return {
            role: str(spec.get("driver", "?"))
            for role, spec in self.instruments.items()
            if isinstance(spec, dict)
        }


def _require(mapping: dict[str, Any], key: str, where: str) -> Any:
    if key not in mapping:
        raise ConfigError(f"bench_config: missing '{key}' in {where}")
    return mapping[key]


def load_config(path: str | Path) -> BenchConfig:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"bench_config not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"bench_config {path.name}: expected a mapping at the top level")

    for key in _REQUIRED_TOP:
        _require(data, key, "top level")

    bench = data["bench"]
    if not isinstance(bench, dict):
        raise ConfigError("bench_config: 'bench' must be a mapping")

    instruments = data["instruments"]
    if not isinstance(instruments, dict):
        raise ConfigError("bench_config: 'instruments' must be a mapping")

    missing = [role for role in _REQUIRED_ROLES if role not in instruments]
    if missing:
        raise ConfigError(
            f"bench_config: no driver configured for role(s) {missing}. "
            f"Every one of {list(_REQUIRED_ROLES)} must be present."
        )

    try:
        sim = SimConfig.from_mapping(data.get("simulation"))
    except ValueError as exc:
        raise ConfigError(f"bench_config {path.name}: {exc}") from exc

    return BenchConfig(
        name=str(_require(bench, "name", "bench")),
        simulate=bool(bench.get("simulate", True)),
        instruments=instruments,
        sim=sim,
        raw=data,
        path=path,
    )

"""Layer 2 — test-plan loading and the pure math helpers.

A *plan* is a CSV table: one row per scenario, columns for the spec values
(trigger, tolerance, timing limits). Re-targeting the framework to a new device
means editing the CSV and flipping ``enabled`` — no code change. The math helpers
here are deliberately pure functions so they can be unit-tested without any
hardware or simulation.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Scenario:
    name: str
    enabled: bool
    trigger: float
    tolerance_pct: float
    detect_max_ms: float
    hold_min_s: float
    extra: dict[str, str]


def _to_bool(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def load_plan(path: str | Path) -> list[Scenario]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"plan not found: {path}")
    scenarios: list[Scenario] = []
    seen: set[str] = set()
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            name = (row.get("name") or "").strip()
            if not name:
                continue
            if name in seen:
                raise ValueError(f"plan {path.name}: duplicate scenario '{name}'")
            seen.add(name)
            known = {"name", "enabled", "trigger", "tolerance_pct", "detect_max_ms", "hold_min_s"}
            scenarios.append(
                Scenario(
                    name=name,
                    enabled=_to_bool(row.get("enabled", "false")),
                    trigger=float(row.get("trigger", 0) or 0),
                    tolerance_pct=float(row.get("tolerance_pct", 0) or 0),
                    detect_max_ms=float(row.get("detect_max_ms", 0) or 0),
                    hold_min_s=float(row.get("hold_min_s", 0) or 0),
                    extra={k: v for k, v in row.items() if k not in known},
                )
            )
    return scenarios


def enabled_scenarios(scenarios: list[Scenario]) -> list[Scenario]:
    return [s for s in scenarios if s.enabled]


# --- pure spec math ------------------------------------------------------
def tolerance_window(trigger: float, tolerance_pct: float) -> tuple[float, float]:
    delta = trigger * tolerance_pct / 100.0
    return trigger - delta, trigger + delta


def within_tolerance(measured: float, trigger: float, tolerance_pct: float) -> bool:
    lo, hi = tolerance_window(trigger, tolerance_pct)
    return lo <= measured <= hi


def timing_ok(measured_ms: float, limit_ms: float) -> bool:
    return 0.0 < measured_ms <= limit_ms


def hold_ok(measured_s: float, minimum_s: float) -> bool:
    return measured_s >= minimum_s

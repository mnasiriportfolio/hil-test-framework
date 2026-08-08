"""Layer 2 — test-plan loading and the pure math helpers.

A *plan* is a CSV table: one row per scenario, columns for the spec values
(trigger, tolerance, timing limits). Re-targeting the framework to a new device
means editing the CSV and flipping ``enabled`` — no code change. The math helpers
here are deliberately pure functions so they can be unit-tested without any
hardware or simulation.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Scenario:
    """One row of a plan: the spec, not the device.

    Detection is bounded from *both* sides because real specifications are
    written both ways round. A fast protection states a ceiling — trip within
    5 ms. A slow one states a floor — do not trip before 1200 ms, because a
    protection that fires on inrush is a broken protection. Collapsing the two
    into a single "maximum" silently inverts half of them, and the test still
    goes green because the device is faster than the limit it was never
    supposed to be measured against.

    A blank column means "unbounded on that side", which is how a plan says
    that the spec is silent rather than that the limit is zero.
    """

    name: str
    enabled: bool
    trigger: float
    tolerance_pct: float
    #: earliest an honest detection may occur; 0.0 when the spec is silent
    detect_min_ms: float
    #: latest an honest detection may occur; ``inf`` when the spec is silent
    detect_max_ms: float
    #: ceiling on the contact transit (digital pin asserted -> contact moved)
    relay_set_max_ms: float
    hold_min_s: float
    extra: dict[str, str]

    @property
    def detect_window(self) -> str:
        """The detection spec as a person would write it in a report."""
        lo, hi = self.detect_min_ms, self.detect_max_ms
        if lo <= 0.0 and hi == float("inf"):
            return "unspecified"
        if lo <= 0.0:
            return f"< {hi:g} ms"
        if hi == float("inf"):
            return f">= {lo:g} ms"
        return f"{lo:g}-{hi:g} ms"


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
            known = {
                "name",
                "enabled",
                "trigger",
                "tolerance_pct",
                "detect_min_ms",
                "detect_max_ms",
                "relay_set_max_ms",
                "hold_min_s",
            }
            scenarios.append(
                Scenario(
                    name=name,
                    enabled=_to_bool(row.get("enabled", "false")),
                    trigger=float(row.get("trigger", 0) or 0),
                    tolerance_pct=float(row.get("tolerance_pct", 0) or 0),
                    detect_min_ms=_limit(row.get("detect_min_ms"), default=0.0),
                    detect_max_ms=_limit(row.get("detect_max_ms"), default=float("inf")),
                    relay_set_max_ms=_limit(row.get("relay_set_max_ms"), default=float("inf")),
                    hold_min_s=float(row.get("hold_min_s", 0) or 0),
                    extra={k: v for k, v in row.items() if k not in known},
                )
            )
    return scenarios


def _limit(raw: str | None, default: float) -> float:
    """A blank limit column means 'the spec is silent', not 'the limit is 0'.

    Reading a missing ceiling as 0 would fail every case; reading a missing
    floor as 0 is exactly right. So the caller states which way an absent
    value should fall open.
    """
    text = (raw or "").strip()
    return default if not text else float(text)


def enabled_scenarios(scenarios: list[Scenario]) -> list[Scenario]:
    return [s for s in scenarios if s.enabled]


# --- pure spec math ------------------------------------------------------
def tolerance_window(trigger: float, tolerance_pct: float) -> tuple[float, float]:
    delta = trigger * tolerance_pct / 100.0
    return trigger - delta, trigger + delta


def within_tolerance(measured: float, trigger: float, tolerance_pct: float) -> bool:
    lo, hi = tolerance_window(trigger, tolerance_pct)
    return lo <= measured <= hi


def timing_ok(measured_ms: float, min_ms: float = 0.0, max_ms: float = float("inf")) -> bool:
    """Is a measured interval inside the spec's window?

    ``measured_ms`` must be finite and positive: an infinite edge time is the
    keyword layer's way of saying "no edge was ever seen", and that is a
    failure however wide the window is. It is not a very fast detection.
    """
    if not math.isfinite(measured_ms) or measured_ms <= 0.0:
        return False
    return min_ms <= measured_ms <= max_ms


def hold_ok(measured_s: float, minimum_s: float) -> bool:
    return measured_s >= minimum_s

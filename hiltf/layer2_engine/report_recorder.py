"""Layer 2 — event-sourced reporting.

The recorder subscribes to the bus and is the *only* thing that accumulates
results. Keywords stay stateless; swapping or adding an output format never
touches test logic. Renders a clean Markdown report with per-step tables.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .event_bus import Event, EventBus

RESULT_TOPIC = "test.result"


@dataclass
class StepResult:
    check: str
    expected: str
    measured: str
    outcome: str  # PASS | CHECK | FAIL


@dataclass
class CaseResult:
    suite: str
    case: str
    steps: list[StepResult] = field(default_factory=list)

    @property
    def outcome(self) -> str:
        outcomes = {s.outcome for s in self.steps}
        if "FAIL" in outcomes:
            return "FAIL"
        if "CHECK" in outcomes:
            return "CHECK"
        return "PASS"


class ReportRecorder:
    def __init__(self, bus: EventBus) -> None:
        self.cases: list[CaseResult] = []
        bus.subscribe(RESULT_TOPIC, self._on_result)

    def _on_result(self, event: Event) -> None:
        p = event.payload
        case = self._get_case(p["suite"], p["case"])
        case.steps.append(
            StepResult(
                check=p["check"],
                expected=p.get("expected", ""),
                measured=p.get("measured", ""),
                outcome=p.get("outcome", "PASS"),
            )
        )

    def _get_case(self, suite: str, case: str) -> CaseResult:
        for c in self.cases:
            if c.suite == suite and c.case == case:
                return c
        c = CaseResult(suite=suite, case=case)
        self.cases.append(c)
        return c

    @property
    def all_passed(self) -> bool:
        return all(c.outcome != "FAIL" for c in self.cases)

    def render_markdown(self) -> str:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines = ["# HIL Test Framework — Test Report", "", f"_Generated {now}_", ""]
        for c in self.cases:
            lines.append(f"## {c.suite} - {c.case}  ->  **{c.outcome}**")
            lines.append("")
            lines.append("| # | Check | Expected | Measured | Outcome |")
            lines.append("|---|-------|----------|----------|---------|")
            for i, s in enumerate(c.steps, 1):
                lines.append(f"| {i} | {s.check} | {s.expected} | {s.measured} | {s.outcome} |")
            lines.append("")
        return "\n".join(lines)

    def write_markdown(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.render_markdown(), encoding="utf-8")
        return path

"""Run every test type against the simulated bench and print a report.

    python examples/run_all.py

No hardware, no Robot Framework needed — pure Python entry point so anyone can
clone the repo and see it work in seconds.
"""

from __future__ import annotations

from pathlib import Path

from hiltf.layer2_engine import (
    EventBus,
    InstrumentController,
    ReportRecorder,
    enabled_scenarios,
    load_config,
    load_plan,
    run_analog_output_case,
    run_harmonic_case,
    run_overcurrent_case,
)

ROOT = Path(__file__).resolve().parents[1]

PLANS = [
    ("overcurrent", "config/overcurrent_plan.csv", run_overcurrent_case),
    ("harmonic", "config/harmonic_plan.csv", run_harmonic_case),
    ("analog_output", "config/analog_out_plan.csv", run_analog_output_case),
]


def main() -> int:
    cfg = load_config(ROOT / "config" / "bench_config.yaml")
    print(f"Bench: {cfg.name} (simulate={cfg.simulate})", flush=True)

    bus = EventBus()
    recorder = ReportRecorder(bus)

    with InstrumentController(cfg) as controller:
        for _kind, plan_path, runner in PLANS:
            for sc in enabled_scenarios(load_plan(ROOT / plan_path)):
                runner(controller, sc, bus)

    report_path = recorder.write_markdown(ROOT / "reports" / "report.md")
    print(recorder.render_markdown(), flush=True)
    print(f"\nMarkdown report written to {report_path}", flush=True)
    print(f"Overall: {'PASS' if recorder.all_passed else 'FAIL'}", flush=True)
    return 0 if recorder.all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

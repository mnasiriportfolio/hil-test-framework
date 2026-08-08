from pathlib import Path

import pytest

from hiltf.layer2_engine.plan import enabled_scenarios, load_plan

ROOT = Path(__file__).resolve().parents[2]


def test_load_overcurrent_plan():
    scenarios = load_plan(ROOT / "config" / "overcurrent_plan.csv")
    names = [s.name for s in scenarios]
    assert names == ["slow_ac", "fast_ac", "slow_low_trigger"]
    assert [s.name for s in enabled_scenarios(scenarios)] == ["slow_ac", "fast_ac"]


def test_extra_columns_preserved():
    scenarios = load_plan(ROOT / "config" / "harmonic_plan.csv")
    assert scenarios[0].extra["harmonic_hz"] == "50"


def test_blank_limits_fall_open_the_right_way():
    """A missing ceiling must not read as zero, or every case fails at once.

    The slow overcurrent row states only a floor and the fast row states only a
    ceiling. Both are legitimate specifications, and the loader has to keep the
    silent side silent rather than defaulting it to a limit nobody wrote.
    """
    by_name = {s.name: s for s in load_plan(ROOT / "config" / "overcurrent_plan.csv")}

    slow = by_name["slow_ac"]
    assert slow.detect_min_ms == 1200.0
    assert slow.detect_max_ms == float("inf")
    assert slow.detect_window == ">= 1200 ms"

    fast = by_name["fast_ac"]
    assert fast.detect_min_ms == 0.0
    assert fast.detect_max_ms == 5.0
    assert fast.detect_window == "< 5 ms"


def test_duplicate_rows_rejected(tmp_path):
    p = tmp_path / "dup.csv"
    p.write_text(
        "name,enabled,trigger,tolerance_pct,detect_max_ms,hold_min_s\n"
        "a,true,1,3,10,3\n"
        "a,true,1,3,10,3\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate"):
        load_plan(p)

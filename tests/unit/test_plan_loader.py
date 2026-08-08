from pathlib import Path

import pytest

from hiltf.layer2_engine.plan import enabled_scenarios, load_plan

ROOT = Path(__file__).resolve().parents[2]


def test_load_overcurrent_plan():
    scenarios = load_plan(ROOT / "config" / "overcurrent_plan.csv")
    names = [s.name for s in scenarios]
    assert names == ["slow_ac", "fast_dc", "validation_ac"]
    assert enabled_scenarios(scenarios)[0].name == "slow_ac"
    assert len(enabled_scenarios(scenarios)) == 1


def test_extra_columns_preserved():
    scenarios = load_plan(ROOT / "config" / "harmonic_plan.csv")
    assert scenarios[0].extra["harmonic_order"] == "3"


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

"""The config -> driver seam.

This is where a typo either fails loudly at load time or silently changes what
the bench does. These tests choose loudly.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hiltf.layer2_engine import ConfigError, load_config
from hiltf.layer3_hal import (
    REGISTRY,
    DriverConfigError,
    DriverContext,
    build_context,
    build_driver,
    expand_env,
    needs_simulated_bench,
)
from hiltf.layer3_hal.simulation import SimConfig, SimulatedBench

ROOT = Path(__file__).resolve().parents[2]
CONFIGS = ROOT / "config"


# --- every shipped config actually loads -----------------------------------
@pytest.mark.parametrize(
    "name", ["bench_config.yaml", "bench_socket.yaml", "bench_visa.yaml"]
)
def test_shipped_configs_load_and_name_registered_drivers(name):
    cfg = load_config(CONFIGS / name)
    assert cfg.name
    for role, driver in cfg.driver_names().items():
        assert driver in REGISTRY, f"{name}: role {role} names unregistered driver {driver!r}"


def test_the_three_configs_differ_only_in_transport():
    """The point of the whole exercise, asserted.

    Same roles, same device behaviour, different drivers. If these ever drift
    apart, "the transport is a config choice" has stopped being true.
    """
    configs = [load_config(CONFIGS / n) for n in
               ("bench_config.yaml", "bench_socket.yaml", "bench_visa.yaml")]
    roles = [set(c.instruments) for c in configs]
    assert roles[0] == roles[1] == roles[2]
    assert configs[0].sim == configs[1].sim == configs[2].sim
    drivers = [tuple(sorted(c.driver_names().items())) for c in configs]
    assert len(set(drivers)) == 3, "the configs must actually select different drivers"


# --- config validation -----------------------------------------------------
def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "bench.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_missing_role_is_rejected(tmp_path):
    path = _write(tmp_path, """
bench: {name: partial}
instruments:
  signal_generator: {driver: sim_signal_generator}
""")
    with pytest.raises(ConfigError, match="multimeter"):
        load_config(path)


def test_unknown_simulation_key_is_rejected(tmp_path):
    """A misspelled device parameter must not silently keep the default."""
    path = _write(tmp_path, """
bench: {name: typo}
instruments:
  signal_generator: {driver: sim_signal_generator}
  multimeter: {driver: sim_multimeter}
  oscilloscope: {driver: sim_oscilloscope}
  dut: {driver: sim_dut}
simulation:
  overcurent_trigger_a: 200.0
""")
    with pytest.raises(ConfigError, match="overcurent_trigger_a"):
        load_config(path)


def test_missing_file_is_a_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="bench_config not found"):
        load_config(tmp_path / "nope.yaml")


# --- the registry ----------------------------------------------------------
@pytest.fixture()
def ctx():
    return DriverContext(bench=SimulatedBench(SimConfig()), root=ROOT)


def test_unknown_driver_lists_the_alternatives(ctx):
    with pytest.raises(DriverConfigError, match="unknown driver 'sim_toaster'"):
        build_driver("multimeter", {"driver": "sim_toaster"}, ctx)


def test_unknown_key_is_rejected_not_ignored(ctx):
    """``timout_s`` would otherwise keep the default and be found on a bench."""
    with pytest.raises(DriverConfigError, match="timout_s"):
        build_driver(
            "multimeter",
            {"driver": "lan_multimeter", "address": "127.0.0.1", "timout_s": 5},
            ctx,
        )


def test_missing_required_key_is_named(ctx):
    with pytest.raises(DriverConfigError, match="address"):
        build_driver("multimeter", {"driver": "lan_multimeter"}, ctx)


def test_missing_driver_key_is_named(ctx):
    with pytest.raises(DriverConfigError, match="missing required key 'driver'"):
        build_driver("multimeter", {"address": "127.0.0.1"}, ctx)


def test_builds_each_transport(ctx):
    sim = build_driver("multimeter", {"driver": "sim_multimeter"}, ctx)
    lan = build_driver(
        "multimeter", {"driver": "lan_multimeter", "address": "10.0.0.5", "port": 5025}, ctx
    )
    dut = build_driver("dut", {"driver": "udp_dut", "address": "10.0.0.9"}, ctx)
    assert type(sim).__name__ == "SimMultimeter"
    assert (lan.io.host, lan.io.port) == ("10.0.0.5", 5025)
    assert (dut.host, dut.port) == ("10.0.0.9", 50000)


# --- environment expansion -------------------------------------------------
def test_env_expansion_prefers_the_environment(monkeypatch):
    monkeypatch.setenv("HILTF_BENCH_HOST", "bench")
    assert expand_env("${HILTF_BENCH_HOST:-127.0.0.1}") == "bench"


def test_env_expansion_falls_back_to_the_default(monkeypatch):
    monkeypatch.delenv("HILTF_BENCH_HOST", raising=False)
    assert expand_env("${HILTF_BENCH_HOST:-127.0.0.1}") == "127.0.0.1"


def test_env_expansion_without_a_default_is_an_error(monkeypatch):
    monkeypatch.delenv("HILTF_NOT_SET", raising=False)
    with pytest.raises(DriverConfigError, match="HILTF_NOT_SET"):
        expand_env("${HILTF_NOT_SET}")


def test_env_expansion_leaves_non_strings_alone():
    assert expand_env(5025) == 5025
    assert expand_env(None) is None


def test_docker_config_is_reachable_from_a_laptop(monkeypatch):
    """One file serves both compose and a developer, with no code branch."""
    monkeypatch.delenv("HILTF_BENCH_HOST", raising=False)
    cfg = load_config(CONFIGS / "bench_socket.yaml")
    driver = build_driver("multimeter", cfg.instruments["multimeter"], DriverContext(root=ROOT))
    assert driver.io.host == "127.0.0.1"

    monkeypatch.setenv("HILTF_BENCH_HOST", "bench")
    driver = build_driver("multimeter", cfg.instruments["multimeter"], DriverContext(root=ROOT))
    assert driver.io.host == "bench"


# --- the simulated bench only exists when something asks for it ------------
def test_socket_bench_creates_no_simulation():
    cfg = load_config(CONFIGS / "bench_socket.yaml")
    assert needs_simulated_bench(cfg.instruments) is False
    assert build_context(cfg.instruments, cfg.sim, ROOT).bench is None


def test_simulated_bench_is_created_and_shared():
    cfg = load_config(CONFIGS / "bench_config.yaml")
    context = build_context(cfg.instruments, cfg.sim, ROOT)
    assert context.bench is not None
    sg = build_driver("signal_generator", cfg.instruments["signal_generator"], context)
    dmm = build_driver("multimeter", cfg.instruments["multimeter"], context)
    # One bench behind all of them, or the generator's output would be invisible
    # to the meter.
    assert sg.bench is dmm.bench is context.bench


def test_simulated_driver_without_a_bench_is_a_clear_error():
    with pytest.raises(DriverConfigError, match="needs a simulated bench"):
        build_driver("multimeter", {"driver": "sim_multimeter"}, DriverContext(bench=None))

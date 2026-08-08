"""The layering rules, enforced by tests instead of by good intentions.

Every README in the world claims a clean architecture. These tests are what
make the claim checkable, and they exist because each one guards a mistake that
is easy to make and invisible once made:

* A keyword that reads the simulation directly still passes every test — on the
  simulated bench. It fails only on hardware, which is the expensive place to
  find out.
* A Layer 2 module that imports a concrete driver has quietly deleted the
  abstraction; the code keeps working and the next transport becomes a rewrite.
* A ``.robot`` file with an IP address in it means the test specification now
  depends on the wiring, and a bench move becomes an edit to the test suite.

The Python checks parse the **AST** rather than grepping the text. A regex over
source cannot tell a real call from the same words inside a docstring, and a
test that fails on its own explanation is a test people delete.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LAYER1 = ROOT / "hiltf" / "layer1_suites"
LAYER2 = ROOT / "hiltf" / "layer2_engine"


def _sources(folder: Path, suffix: str = "*.py") -> list[Path]:
    return sorted(folder.glob(suffix))


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _referenced_names(tree: ast.Module) -> set[str]:
    """Every identifier the *code* uses — docstrings and comments excluded."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.alias):
            names.add(node.name.split(".")[0])
            names.add(node.name)
    return names


def _imported_modules(tree: ast.Module) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module.split(".")[0])
            modules.update(alias.name for alias in node.names)
    return modules


# --- Layer 2 knows nothing about transports -------------------------------
#: identifiers that only exist because a specific transport exists
FORBIDDEN_IN_LAYER2 = {
    "SimulatedBench",
    "SimSignalGenerator",
    "SimMultimeter",
    "SimOscilloscope",
    "SimDeviceUnderTest",
    "LanSignalGenerator",
    "LanMultimeter",
    "LanOscilloscope",
    "VisaSession",
    "VisaSignalGenerator",
    "VisaMultimeter",
    "VisaOscilloscope",
    "BinaryUdpDut",
    "ScpiSocket",
    "scpi_commands",
    "scpi_socket",
    "dut_protocol",
    "sim_drivers",
    "lan_drivers",
    "visa_drivers",
}

#: libraries that mean "this module implements a transport"
TRANSPORT_LIBRARIES = {"socket", "pyvisa", "struct", "socketserver"}


@pytest.mark.parametrize("path", _sources(LAYER2), ids=lambda p: p.name)
def test_layer2_never_names_a_transport(path: Path) -> None:
    used = _referenced_names(_tree(path)) & FORBIDDEN_IN_LAYER2
    assert not used, (
        f"{path.name} uses transport-specific names {sorted(used)}. Layer 2 must "
        "speak only the Protocols in layer3_hal/interfaces.py — otherwise the "
        "same suites cannot run over all four transports."
    )


@pytest.mark.parametrize("path", _sources(LAYER2) + _sources(LAYER1), ids=lambda p: p.name)
def test_only_layer3_imports_transport_libraries(path: Path) -> None:
    used = _imported_modules(_tree(path)) & TRANSPORT_LIBRARIES
    assert not used, f"{path.name} imports {sorted(used)}; that belongs in Layer 3"


def test_keywords_never_reach_into_the_simulation() -> None:
    """The specific regression: ``controller.bench.hold_time_s(...)``.

    Reading the answer out of the simulation object makes a keyword pass on the
    simulated bench and be meaningless everywhere else. The hold duration must
    be *measured* from the captured trace, like on a real scope.
    """
    tree = _tree(LAYER2 / "keywords.py")
    offenders = [
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "bench"
    ]
    assert not offenders, (
        "keywords.py reaches into the simulation. Every value must come back "
        "through a Layer 3 Protocol method."
    )


def test_controller_exposes_no_simulation_object() -> None:
    """A socket-configured run must not have a simulation in the process."""
    from hiltf.layer2_engine import InstrumentController, load_config

    controller = InstrumentController(load_config(ROOT / "config" / "bench_socket.yaml"))
    assert not hasattr(controller, "bench"), (
        "InstrumentController must not expose a simulation object; whether one "
        "exists at all is decided inside Layer 3 (factory.build_context)."
    )


# --- Layer 1 knows nothing about wiring ------------------------------------
_IP_LIKE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
_SCPI_LIKE = re.compile(r"\b(?:SOUR\d|MEAS:|OUTP\d|WAV:|\*IDN|\*RST|TCPIP\d|::SOCKET)\b")


@pytest.mark.parametrize("path", _sources(LAYER1, "*.robot"), ids=lambda p: p.name)
def test_robot_suites_contain_no_addresses_or_scpi(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    assert not _IP_LIKE.search(source), f"{path.name} contains an IP address"
    assert not _SCPI_LIKE.search(source), f"{path.name} contains SCPI"


@pytest.mark.parametrize("path", _sources(LAYER1, "*.robot"), ids=lambda p: p.name)
def test_robot_suites_select_the_bench_by_variable(path: Path) -> None:
    """Every suite must be retargetable without editing it."""
    source = path.read_text(encoding="utf-8")
    assert "${CONFIG}" in source, (
        f"{path.name} does not take ${{CONFIG}}, so it cannot be pointed at "
        "another bench from the command line"
    )


# --- the registry covers every role ---------------------------------------
def test_every_role_has_at_least_one_registered_driver() -> None:
    from hiltf.layer3_hal import REGISTRY

    for role in ("signal_generator", "multimeter", "oscilloscope", "dut"):
        assert any(name.endswith(role) for name in REGISTRY), f"no driver for {role}"

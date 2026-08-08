"""Layer 3 (HAL) — instrument drivers over **PyVISA**.

Same Protocols, same SCPI strings (from :mod:`hiltf.layer3_hal.scpi_commands`)
as the raw-socket drivers next door — a different way of getting the bytes to
the instrument. Having both is deliberate: it shows the HAL boundary is real,
and it reflects how a mixed bench actually looks, where a GPIB or USBTMC box
has to go through VISA and an Ethernet box does not need to.

**Three backends, selected by config, no code change:**

``visa_backend: "@sim"``
    `pyvisa-sim <https://pyvisa.readthedocs.io/projects/pyvisa-sim/>`_ answers
    from the device definition in ``hiltf/emulators/visa_sim.yaml``. Pure
    Python, no network, no VISA library. This is what makes it possible to
    unit-test *the driver's SCPI contract* — does it send the right header, does
    it parse the reply — in CI, on any machine.

``visa_backend: "@py"``
    `pyvisa-py <https://pyvisa.readthedocs.io/projects/pyvisa-py/>`_, a pure
    Python VISA implementation. Pointed at
    ``TCPIP0::bench::5025::SOCKET`` it talks to this repo's own bench emulator,
    so the *whole* PyVISA path — resource manager, session, termination
    handling, binary block transfer — runs end to end against a live TCP
    server with real bench physics behind it. That is what the ``visa-runner``
    service in ``docker-compose.yml`` exercises.

``visa_backend: ""``
    The system VISA library (NI-VISA / Keysight IO Libraries). Same driver
    code, real instruments, GPIB/USB/PXI included. Changing to this is a line
    in ``bench_config.yaml``.

PyVISA specifics worth knowing, all of which bite in practice:

* ``timeout`` is in **milliseconds**, unlike every socket API.
* A ``::SOCKET`` resource has **no default termination**. If you do not set
  ``read_termination``/``write_termination`` yourself, reads hang until the
  timeout and writes arrive without a delimiter. INSTR resources differ. This
  is the single most common PyVISA "it just hangs" cause.
* Bulk waveforms should go through :meth:`query_binary_values`, which parses
  the IEEE 488.2 ``#<n><len>`` header for you — do not hand-roll it, and do
  not fetch megabytes as ASCII.
* A timeout surfaces as ``VisaIOError`` with
  ``StatusCode.error_timeout`` — distinguishable from a real I/O failure, and
  the two deserve different handling.
"""

from __future__ import annotations

from typing import Any

from .base_driver import BaseDriver
from .interfaces import Waveform
from .scpi_commands import (
    TERMINATOR,
    block_header_length,
    clear_harmonics,
    decode_payload,
    digitize_relay,
    get_output,
    idn,
    interrupt_output,
    measure_ac_current,
    measure_dc_voltage,
    measure_harmonic_current,
    measure_line_kv,
    operation_complete,
    set_amplitude_vpp,
    set_frequency_hz,
    set_function,
    set_harmonic_vpp,
    set_offset_v,
    set_output,
    waveform_data,
    waveform_sample_rate,
)

try:  # pragma: no cover - exercised by the import-guard test
    import pyvisa
    from pyvisa import constants as visa_constants
    from pyvisa.errors import VisaIOError

    PYVISA_AVAILABLE = True
except ImportError:  # pragma: no cover
    pyvisa = None  # type: ignore[assignment]
    visa_constants = None  # type: ignore[assignment]

    class VisaIOError(Exception):  # type: ignore[no-redef]
        """Stand-in so except-clauses stay valid when PyVISA is absent."""

        error_code = None

    PYVISA_AVAILABLE = False


_INSTALL_HINT = (
    "PyVISA is not installed. Install the optional extra:  pip install -e '.[visa]'\n"
    "That pulls pyvisa + pyvisa-sim + pyvisa-py, none of which need a vendor "
    "VISA runtime. A system VISA library (NI-VISA) is only needed for the "
    "'' backend against real hardware."
)


class VisaSession:
    """A thin, honest wrapper around a PyVISA message-based resource."""

    def __init__(
        self,
        resource: str,
        backend: str = "@sim",
        sim_yaml: str | None = None,
        timeout_ms: float = 10_000.0,
        name: str = "visa",
    ) -> None:
        self.resource = resource
        self.backend = backend
        self.sim_yaml = sim_yaml
        self.timeout_ms = float(timeout_ms)
        self.name = name
        self._rm: Any = None
        self._inst: Any = None

    # --- backend selection ------------------------------------------------
    def _backend_spec(self) -> str:
        """Build the string PyVISA's ResourceManager takes.

        ``pyvisa-sim`` is selected as ``<path-to-yaml>@sim``; the plain
        ``@sim`` form uses the package's own default device set, which is not
        this bench, so a configured YAML path is strongly preferred.
        """
        if self.backend == "@sim" and self.sim_yaml:
            return f"{self.sim_yaml}@sim"
        return self.backend

    def open(self) -> None:
        if not PYVISA_AVAILABLE:
            raise ImportError(_INSTALL_HINT)
        if self._inst is not None:
            return
        self._rm = pyvisa.ResourceManager(self._backend_spec())
        self._inst = self._rm.open_resource(self.resource)
        # Explicit on purpose — see the module docstring. A ::SOCKET resource
        # has no default termination and will hang without these two lines.
        self._inst.read_termination = TERMINATOR
        self._inst.write_termination = TERMINATOR
        self._inst.timeout = self.timeout_ms  # milliseconds

    def close(self) -> None:
        try:
            if self._inst is not None:
                self._inst.close()
        finally:
            self._inst = None
            if self._rm is not None:
                self._rm.close()
            self._rm = None

    @property
    def is_open(self) -> bool:
        return self._inst is not None

    def _require(self) -> Any:
        if self._inst is None:
            raise RuntimeError(f"{self.name}: VISA session is not open")
        return self._inst

    # --- operations -------------------------------------------------------
    def write(self, command: str) -> None:
        self._require().write(command)

    def query(self, command: str) -> str:
        return str(self._require().query(command)).strip()

    def query_float(self, command: str) -> float:
        raw = self.query(command)
        try:
            return float(raw)
        except ValueError as exc:
            raise RuntimeError(f"{self.name}: {command!r} returned non-numeric {raw!r}") from exc

    def sync(self) -> None:
        """Block until every command written on this session has executed.

        See :func:`hiltf.layer3_hal.scpi_commands.operation_complete` — without
        it, "configure the generator, then read the meter" is a race between
        two independent VISA sessions.
        """
        self.query(operation_complete())

    def safe_query(self, command: str, default: str = "N/A") -> str:
        """Query, absorbing *timeouts only*.

        A timeout means "this model does not implement that header" and is
        survivable. Any other VisaIOError is a genuine transport fault and is
        re-raised — swallowing those is how a bench run ends up green while the
        instrument was unplugged the whole time.
        """
        try:
            return self.query(command)
        except VisaIOError as exc:
            timed_out = (
                visa_constants is not None
                and getattr(exc, "error_code", None) == visa_constants.StatusCode.error_timeout
            )
            if timed_out:
                self.clear()
                return default
            raise

    def clear(self) -> None:
        """Clear the device's I/O buffers and status, ignoring failures."""
        inst = self._inst
        if inst is None:
            return
        try:
            inst.clear()
        except Exception:  # noqa: BLE001 - a failing clear must not mask the original fault
            pass

    def query_binary(self, command: str) -> list[float]:
        """Fetch an IEEE 488.2 definite-length block as floats.

        PyVISA offers :meth:`query_binary_values`, which is the idiomatic call
        and the right one for most instruments. It is deliberately not used
        here, for a specific reason:

        A ``::SOCKET`` resource has to have ``read_termination`` set for its
        *text* queries to work at all. But a float32 sample can be any four
        bytes, including ``0x0A 0x0A 0x0A 0x0A`` — a perfectly ordinary value
        that is also four termination characters. With termination enabled, a
        read can stop in the middle of the payload, and how much of that the
        convenience helper absorbs depends on the backend and the resource
        type. The failure is silent: a short waveform, plausible numbers, and
        every edge time computed from it is wrong.

        So termination is switched off for the transfer and the block is read
        by *count*: the header says how many bytes follow, and exactly that
        many are taken. ``read_bytes(n)`` means n bytes on every backend.
        """
        inst = self._require()
        previous = inst.read_termination
        inst.read_termination = None
        try:
            inst.write(command)
            prefix = inst.read_bytes(2)  # b"#" plus the digit-count digit
            if prefix[:1] != b"#":
                raise RuntimeError(f"{self.name}: expected block prefix '#', got {prefix!r}")
            ndigits = block_header_length(prefix[1:2])
            declared = int(inst.read_bytes(ndigits))
            payload = inst.read_bytes(declared)
            inst.read_bytes(len(TERMINATOR))  # the block's trailing terminator
        finally:
            inst.read_termination = previous
        return decode_payload(payload)

    def __enter__(self) -> VisaSession:
        self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def list_resources(backend: str = "", sim_yaml: str | None = None) -> tuple[str, ...]:
    """Enumerate VISA resources — the first thing to run when a bench is quiet."""
    if not PYVISA_AVAILABLE:
        raise ImportError(_INSTALL_HINT)
    spec = f"{sim_yaml}@sim" if backend == "@sim" and sim_yaml else backend
    rm = pyvisa.ResourceManager(spec)
    try:
        return tuple(rm.list_resources())
    finally:
        rm.close()


class _VisaDriver(BaseDriver):
    """Shared lifecycle for anything reached through PyVISA."""

    def __init__(
        self,
        resource: str,
        backend: str = "@sim",
        sim_yaml: str | None = None,
        timeout_s: float = 10.0,
        name: str = "VISA",
    ) -> None:
        super().__init__(name)
        self.io = VisaSession(
            resource, backend=backend, sim_yaml=sim_yaml, timeout_ms=timeout_s * 1000.0, name=name
        )

    def connect(self) -> None:
        self.io.open()
        super().connect()

    def disconnect(self) -> None:
        try:
            self.io.close()
        finally:
            super().disconnect()

    def identify(self) -> str:
        self._require_connection()
        return self.io.safe_query(idn(), default="N/A")


class VisaSignalGenerator(_VisaDriver):
    def __init__(
        self,
        resource: str,
        backend: str = "@sim",
        sim_yaml: str | None = None,
        timeout_s: float = 10.0,
        name: str = "VISA-AFG",
    ) -> None:
        super().__init__(resource, backend, sim_yaml, timeout_s, name)

    def configure_sine(
        self, channel: int, amplitude_vpp: float, frequency_hz: float, offset_v: float = 0.0
    ) -> None:
        self._require_connection()
        self.io.write(set_function(channel, "SIN"))
        self.io.write(clear_harmonics(channel))
        self.io.write(set_amplitude_vpp(channel, amplitude_vpp))
        self.io.write(set_frequency_hz(channel, frequency_hz))
        self.io.write(set_offset_v(channel, offset_v))
        self.io.sync()

    def configure_dc(self, channel: int, level_v: float) -> None:
        self._require_connection()
        self.io.write(set_function(channel, "DC"))
        self.io.write(clear_harmonics(channel))
        self.io.write(set_amplitude_vpp(channel, level_v))
        self.io.sync()

    def add_harmonic(self, channel: int, order: int, amplitude_vpp: float) -> None:
        self._require_connection()
        self.io.write(set_harmonic_vpp(channel, order, amplitude_vpp))
        self.io.sync()

    def output_on(self, channel: int) -> None:
        self._require_connection()
        self.io.write(set_output(channel, True))
        self.io.sync()

    def output_off(self, channel: int) -> None:
        self._require_connection()
        self.io.write(set_output(channel, False))
        self.io.sync()

    def output_state(self, channel: int) -> bool:
        self._require_connection()
        return self.io.query(get_output(channel)).strip() in {"1", "ON"}

    def interrupt_output(self, channel: int, duration_ms: float) -> None:
        self._require_connection()
        self.io.write(interrupt_output(channel, duration_ms))
        self.io.sync()


class VisaMultimeter(_VisaDriver):
    def __init__(
        self,
        resource: str,
        backend: str = "@sim",
        sim_yaml: str | None = None,
        timeout_s: float = 10.0,
        name: str = "VISA-DMM",
    ) -> None:
        super().__init__(resource, backend, sim_yaml, timeout_s, name)

    def measure_dc_voltage(self) -> float:
        self._require_connection()
        return self.io.query_float(measure_dc_voltage())

    def measure_line_kv(self) -> float:
        self._require_connection()
        return self.io.query_float(measure_line_kv())

    def measure_ac_current_rms(self) -> float:
        self._require_connection()
        return self.io.query_float(measure_ac_current())

    def measure_harmonic_current_rms(self) -> float:
        self._require_connection()
        return self.io.query_float(measure_harmonic_current())


class VisaOscilloscope(_VisaDriver):
    def __init__(
        self,
        resource: str,
        backend: str = "@sim",
        sim_yaml: str | None = None,
        timeout_s: float = 30.0,
        name: str = "VISA-OSC",
    ) -> None:
        super().__init__(resource, backend, sim_yaml, timeout_s, name)

    def capture_relay(self, relay: str, gate_ms: float) -> Waveform:
        self._require_connection()
        self.io.write(digitize_relay(relay, gate_ms))
        sample_rate = self.io.query_float(waveform_sample_rate())
        samples = self.io.query_binary(waveform_data())
        return Waveform(dt_s=1.0 / sample_rate, samples=samples)


def _main() -> int:  # pragma: no cover - operator convenience
    """``python -m hiltf.layer3_hal.visa_drivers`` — list VISA resources."""
    import argparse

    parser = argparse.ArgumentParser(description="List VISA resources.")
    parser.add_argument("--backend", default="", help="'' (system VISA), '@py', or '@sim'")
    parser.add_argument("--sim-yaml", default=None, help="device YAML for the @sim backend")
    args = parser.parse_args()

    for res in list_resources(args.backend, args.sim_yaml):
        print(res, flush=True)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())

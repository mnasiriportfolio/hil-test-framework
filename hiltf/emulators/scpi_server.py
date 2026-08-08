"""A SCPI instrument emulator, served over TCP.

This is the other half of the socket and PyVISA drivers: a real listener,
speaking the real command set from :mod:`hiltf.layer3_hal.scpi_commands`, with
the real :class:`~hiltf.layer3_hal.simulation.SimulatedBench` physics behind it.

Why a server and not a mock object: a mock proves the driver calls the methods
the test author imagined. A socket proves the driver survives partial reads,
interleaved responses, binary payloads containing newline bytes, and a peer
that answers nothing at all. Every one of those has broken a real instrument
driver, and none of them are reachable with a mock.

Two behaviours are deliberately unhelpful, because real instruments are:

* An **unrecognised header gets no reply at all** — it lands in the error queue
  and the client waits. That is what a firmware revision without a given
  command does, and it is why ``ScpiSocket.safe_query`` exists.
* ``SYST:ERR?`` returns the SCPI-99 ``<code>,"<message>"`` pair and pops one
  entry, so a driver that never drains the queue eventually reads stale errors.

Each TCP connection gets its own interpreter — matching a real rack, where each
instrument owns its acquisition state — while all of them share one bench, so
what the generator drives is what the DMM and the scope see.
"""

from __future__ import annotations

import re
import socketserver
import threading
from collections import deque
from collections.abc import Callable

from ..layer3_hal import scpi_commands as scpi
from ..layer3_hal.simulation import SimulatedBench

_NUM = r"[-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?"

NO_ERROR = '+0,"No error"'
ERR_UNDEFINED_HEADER = '-113,"Undefined header"'
ERR_DATA_OUT_OF_RANGE = '-222,"Data out of range"'


class ScpiInterpreter:
    """Turns one SCPI line into the bytes to send back (or ``None``).

    Pure with respect to I/O — it never touches a socket, so the entire command
    set is unit-testable directly.
    """

    def __init__(self, bench: SimulatedBench, sample_rate_hz: float = 50_000.0) -> None:
        self.bench = bench
        self.sample_rate_hz = float(sample_rate_hz)
        self._errors: deque[str] = deque(maxlen=32)
        self._trace: list[float] = []
        self._trace_dt: float = 0.0
        self._handlers: list[tuple[re.Pattern[str], Callable[..., bytes | None]]] = [
            (re.compile(r"^\*IDN\?$"), self._idn),
            (re.compile(r"^\*RST$"), self._rst),
            (re.compile(r"^\*CLS$"), self._cls),
            (re.compile(r"^\*OPC\?$"), self._opc),
            (re.compile(r"^SYST:ERR\?$"), self._syst_err),
            (re.compile(r"^SOUR(\d+):FUNC (SIN|DC)$"), self._set_function),
            (re.compile(r"^SOUR(\d+):FUNC\?$"), self._get_function),
            (re.compile(rf"^SOUR(\d+):VOLT:OFFS ({_NUM})$"), self._set_offset),
            (re.compile(r"^SOUR(\d+):VOLT:OFFS\?$"), self._get_offset),
            (re.compile(rf"^SOUR(\d+):VOLT ({_NUM})$"), self._set_amplitude),
            (re.compile(r"^SOUR(\d+):VOLT\?$"), self._get_amplitude),
            (re.compile(rf"^SOUR(\d+):FREQ ({_NUM})$"), self._set_frequency),
            (re.compile(r"^SOUR(\d+):FREQ\?$"), self._get_frequency),
            (re.compile(r"^SOUR(\d+):HARM:CLE$"), self._clear_harmonics),
            (re.compile(rf"^SOUR(\d+):HARM(\d+):VOLT ({_NUM})$"), self._set_harmonic),
            (re.compile(r"^OUTP(\d+) (ON|OFF|1|0)$"), self._set_output),
            (re.compile(r"^OUTP(\d+)\?$"), self._get_output),
            (re.compile(r"^MEAS:VOLT:DC\?$"), self._meas_dc_voltage),
            (re.compile(r"^MEAS:CURR:AC:HARM\?$"), self._meas_harmonic_current),
            (re.compile(r"^MEAS:CURR:AC\?$"), self._meas_ac_current),
            (re.compile(rf'^DIG:REL "?([A-Za-z_][A-Za-z0-9_]*)"?,({_NUM})$'), self._digitize),
            (re.compile(r"^WAV:SRAT\?$"), self._wav_sample_rate),
            (re.compile(r"^WAV:POIN\?$"), self._wav_points),
            (re.compile(r"^WAV:DATA\?$"), self._wav_data),
        ]

    # --- entry point ------------------------------------------------------
    def execute(self, line: str) -> bytes | None:
        """Execute one command. ``None`` means "say nothing back"."""
        command = line.strip()
        if not command:
            return None
        for pattern, handler in self._handlers:
            match = pattern.match(command)
            if match:
                try:
                    return handler(*match.groups())
                except (KeyError, ValueError):
                    # e.g. a channel or relay the bench does not have
                    self._errors.append(ERR_DATA_OUT_OF_RANGE)
                    return None
        # Unrecognised. Queue the error and stay silent, exactly like the real
        # thing — the client's read will time out.
        self._errors.append(ERR_UNDEFINED_HEADER)
        return None

    # --- helpers ----------------------------------------------------------
    @staticmethod
    def _text(value: str) -> bytes:
        return (value + scpi.TERMINATOR).encode("ascii")

    def _number(self, value: float) -> bytes:
        return self._text(f"{value:.6f}")

    # --- common -----------------------------------------------------------
    def _idn(self) -> bytes:
        return self._text(scpi.IDN)

    def _rst(self) -> None:
        self.bench.reset()
        self._trace, self._trace_dt = [], 0.0
        return None

    def _cls(self) -> None:
        self._errors.clear()
        return None

    def _opc(self) -> bytes:
        """``*OPC?`` — answers only once everything queued before it has run.

        That property is free here: the connection handler reads and executes
        commands strictly in order, so by the time this line is reached, every
        earlier command on this session has already been applied to the shared
        bench. Which is exactly the guarantee a client needs before going off
        to read a *different* instrument.
        """
        return self._text("1")

    def _syst_err(self) -> bytes:
        return self._text(self._errors.popleft() if self._errors else NO_ERROR)

    # --- generator --------------------------------------------------------
    def _set_function(self, channel: str, shape: str) -> None:
        ch = int(channel)
        if shape == "SIN":
            state = self.bench.channel(ch)
            self.bench.configure_sine(ch, state.amplitude_vpp, state.frequency_hz, state.offset_v)
        else:
            self.bench.configure_dc(ch, self.bench.channel(ch).amplitude_vpp)
        return None

    def _get_function(self, channel: str) -> bytes:
        return self._text(self.bench.channel(int(channel)).waveform)

    def _set_amplitude(self, channel: str, value: str) -> None:
        self.bench.channel(int(channel)).amplitude_vpp = float(value)
        return None

    def _get_amplitude(self, channel: str) -> bytes:
        return self._number(self.bench.channel(int(channel)).amplitude_vpp)

    def _set_offset(self, channel: str, value: str) -> None:
        self.bench.channel(int(channel)).offset_v = float(value)
        return None

    def _get_offset(self, channel: str) -> bytes:
        return self._number(self.bench.channel(int(channel)).offset_v)

    def _set_frequency(self, channel: str, value: str) -> None:
        self.bench.channel(int(channel)).frequency_hz = float(value)
        return None

    def _get_frequency(self, channel: str) -> bytes:
        return self._number(self.bench.channel(int(channel)).frequency_hz)

    def _set_harmonic(self, channel: str, order: str, value: str) -> None:
        self.bench.add_harmonic(int(channel), int(order), float(value))
        return None

    def _clear_harmonics(self, channel: str) -> None:
        self.bench.clear_harmonics(int(channel))
        return None

    def _set_output(self, channel: str, state: str) -> None:
        self.bench.set_output(int(channel), state in {"ON", "1"})
        return None

    def _get_output(self, channel: str) -> bytes:
        return self._text("1" if self.bench.output_state(int(channel)) else "0")

    # --- measurements -----------------------------------------------------
    def _meas_dc_voltage(self) -> bytes:
        return self._number(self.bench.voltage_dc_v())

    def _meas_ac_current(self) -> bytes:
        return self._number(self.bench.current_rms_a())

    def _meas_harmonic_current(self) -> bytes:
        return self._number(self.bench.harmonic_current_rms_a())

    # --- acquisition ------------------------------------------------------
    def _digitize(self, relay: str, gate_ms: str) -> None:
        """Acquire the relay line and hold it for the WAV: queries."""
        self._trace_dt, self._trace = self.bench.relay_trace(
            relay, float(gate_ms), self.sample_rate_hz
        )
        return None

    def _wav_sample_rate(self) -> bytes:
        rate = 1.0 / self._trace_dt if self._trace_dt else self.sample_rate_hz
        return self._number(rate)

    def _wav_points(self) -> bytes:
        return self._text(str(len(self._trace)))

    def _wav_data(self) -> bytes:
        return scpi.encode_block(self._trace) + scpi.TERMINATOR.encode("ascii")


class _ScpiHandler(socketserver.StreamRequestHandler):
    #: responses go out immediately; a bench client is waiting on every one
    wbufsize = 0
    disable_nagle_algorithm = True

    def setup(self) -> None:
        super().setup()
        server: ScpiServer = self.server  # type: ignore[assignment]
        # One interpreter per connection: acquisition state is per-instrument,
        # while the bench underneath is shared by all of them.
        self.interpreter = ScpiInterpreter(server.bench, server.sample_rate_hz)

    def handle(self) -> None:
        while True:
            raw = self.rfile.readline()
            if not raw:
                return  # peer closed
            try:
                line = raw.decode("ascii", errors="replace")
            except Exception:  # noqa: BLE001 - never let one bad line kill the session
                continue
            response = self.interpreter.execute(line)
            if response:
                try:
                    self.wfile.write(response)
                except OSError:
                    return  # client vanished mid-answer


class ScpiServer(socketserver.ThreadingTCPServer):
    """Threaded TCP server exposing one :class:`SimulatedBench` as SCPI."""

    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        bench: SimulatedBench,
        host: str = "127.0.0.1",
        port: int = 5025,
        sample_rate_hz: float = 50_000.0,
    ) -> None:
        self.bench = bench
        self.sample_rate_hz = float(sample_rate_hz)
        super().__init__((host, port), _ScpiHandler)

    @property
    def port(self) -> int:
        """The bound port — resolves port 0 to what the OS actually gave us."""
        return int(self.server_address[1])

    #: ``shutdown()`` cannot return faster than one poll interval, and the
    #: stdlib default of 0.5 s is paid on every emulator teardown — which in a
    #: test suite that starts one per test is most of the runtime.
    POLL_INTERVAL_S = 0.02

    def start_background(self) -> threading.Thread:
        thread = threading.Thread(
            target=self.serve_forever,
            args=(self.POLL_INTERVAL_S,),
            name="scpi-server",
            daemon=True,
        )
        thread.start()
        return thread

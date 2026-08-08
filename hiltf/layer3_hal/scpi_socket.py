"""Layer 3 (HAL) — raw TCP/SCPI transport, standard library only.

No PyVISA, no NI-VISA, no vendor DLL: a socket, a line buffer and a byte
counter. Benchtop instruments that expose a "SCPI raw socket" port (commonly
5025) need nothing more, and a stdlib-only driver installs anywhere and starts
instantly — which matters on a locked-down bench PC.

Three details here are not obvious, and each one is a bug this transport exists
to prevent:

1. **Buffered line reading.** ``recv()`` is not line-oriented. One call can
   return half a response, or three responses at once. Reading a "line" with a
   bare ``recv()`` works until the first instrument that answers quickly, and
   then silently desynchronises the whole session — every later query returns
   the *previous* query's answer. So bytes go into a buffer and lines are cut
   out of it.

2. **Exact-count block reads.** A waveform arrives as
   ``#<ndigits><bytecount><payload>``. The payload is binary and may contain
   ``\\n``, so it must be read by *count*, never by delimiter.

3. **Tolerating unsupported commands.** Instruments differ by model and by
   firmware revision. Querying something the box does not implement gets you
   silence, and the socket times out. :meth:`safe_query` turns that into a
   documented default instead of an aborted test run, and clears the error
   queue so the next query is not answered by the last one's error.
"""
from __future__ import annotations

import socket

from . import scpi_commands as scpi


class ScpiError(RuntimeError):
    """Transport-level failure talking to a SCPI instrument."""


class ScpiSocket:
    """A line- and block-oriented SCPI session over a raw TCP socket."""

    #: how many bytes to ask the kernel for per recv()
    CHUNK = 65536

    def __init__(
        self,
        host: str,
        port: int = 5025,
        timeout_s: float = 5.0,
        name: str = "scpi",
        encoding: str = "ascii",
    ) -> None:
        self.host = host
        self.port = int(port)
        self.timeout_s = float(timeout_s)
        self.name = name
        self.encoding = encoding
        self._sock: socket.socket | None = None
        self._buf = bytearray()

    # --- lifecycle -------------------------------------------------------
    def open(self) -> None:
        if self._sock is not None:
            return
        try:
            self._sock = socket.create_connection((self.host, self.port), self.timeout_s)
        except OSError as exc:
            raise ScpiError(f"{self.name}: cannot reach {self.host}:{self.port} ({exc})") from exc
        self._sock.settimeout(self.timeout_s)
        self._buf.clear()

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None
        self._buf.clear()

    @property
    def is_open(self) -> bool:
        return self._sock is not None

    def _require(self) -> socket.socket:
        if self._sock is None:
            raise ScpiError(f"{self.name}: socket is not open")
        return self._sock

    # --- primitives ------------------------------------------------------
    def write(self, command: str) -> None:
        sock = self._require()
        sock.sendall((command + scpi.TERMINATOR).encode(self.encoding))

    def _fill(self) -> None:
        """Pull one chunk from the socket into the buffer."""
        sock = self._require()
        chunk = sock.recv(self.CHUNK)
        if not chunk:
            raise ScpiError(f"{self.name}: connection closed by instrument")
        self._buf.extend(chunk)

    def read_line(self) -> str:
        """Read one terminated response line out of the buffer.

        Note the loop: it keeps filling until a terminator shows up, and leaves
        anything after that terminator in the buffer for the next call.
        """
        term = scpi.TERMINATOR.encode(self.encoding)
        while term not in self._buf:
            self._fill()
        line, _, rest = bytes(self._buf).partition(term)
        self._buf = bytearray(rest)
        return line.decode(self.encoding, errors="replace").strip()

    def read_exact(self, count: int) -> bytes:
        """Read exactly ``count`` bytes — used for binary block payloads."""
        while len(self._buf) < count:
            self._fill()
        out = bytes(self._buf[:count])
        del self._buf[:count]
        return out

    # --- SCPI operations -------------------------------------------------
    def query(self, command: str) -> str:
        self.write(command)
        return self.read_line()

    def query_float(self, command: str) -> float:
        raw = self.query(command)
        try:
            return float(raw)
        except ValueError as exc:
            raise ScpiError(f"{self.name}: {command!r} returned non-numeric {raw!r}") from exc

    def sync(self) -> None:
        """Block until every command written on this session has executed.

        The one line that turns "set then measure" from a race into a
        sequence. See :func:`hiltf.layer3_hal.scpi_commands.operation_complete`.
        """
        self.query(scpi.operation_complete())

    def safe_query(self, command: str, default: str = "N/A") -> str:
        """Query, but return ``default`` if the instrument does not answer.

        A model or firmware revision that does not implement a header simply
        says nothing and the socket times out. That is a fact about the fleet,
        not a test failure — so it is absorbed here, and the error queue is
        drained so the silence does not shift every later response by one.
        """
        try:
            return self.query(command)
        except (TimeoutError, socket.timeout):
            self._drain()
            return default

    def _drain(self) -> None:
        """Discard anything pending and clear the instrument's error queue."""
        self._buf.clear()
        sock = self._sock
        if sock is None:
            return
        sock.settimeout(0.05)
        try:
            while sock.recv(self.CHUNK):
                pass
        except OSError:
            pass
        finally:
            sock.settimeout(self.timeout_s)
        try:
            self.write(scpi.clear_status())
        except OSError:
            pass

    def query_block(self, command: str) -> list[float]:
        """Query an IEEE 488.2 definite-length block and decode it to floats.

        Read by *count*: the payload is binary and will contain bytes that look
        like terminators.
        """
        self.write(command)
        prefix = self.read_exact(1)
        if prefix != b"#":
            raise ScpiError(f"{self.name}: expected block prefix '#', got {prefix!r}")
        ndigits = scpi.block_header_length(self.read_exact(1))
        declared = int(self.read_exact(ndigits))
        payload = self.read_exact(declared)
        # instruments terminate the block with the usual line terminator
        term = scpi.TERMINATOR.encode(self.encoding)
        if self._buf[:1] == term:
            del self._buf[:1]
        return scpi.decode_payload(payload)

    # --- context manager -------------------------------------------------
    def __enter__(self) -> ScpiSocket:
        self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

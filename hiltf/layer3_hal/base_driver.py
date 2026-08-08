"""Layer 3 (HAL) — the base every driver extends.

Gives every driver the same connect/disconnect lifecycle and context-manager
behaviour, so Layer 2 can write ``with controller.signal_generator as sg:``
without caring what sits underneath — an in-process simulation, a TCP socket,
a PyVISA resource or a UDP datagram protocol.
"""

from __future__ import annotations


class BaseDriver:
    def __init__(self, name: str) -> None:
        self.name = name
        self._connected = False

    # --- lifecycle -------------------------------------------------------
    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def identify(self) -> str:
        """Instrument identity string (``*IDN?`` on a SCPI instrument)."""
        return self.name

    @property
    def is_connected(self) -> bool:
        return self._connected

    def _require_connection(self) -> None:
        if not self._connected:
            raise RuntimeError(f"{self.name}: operation attempted before connect()")

    # --- context manager -------------------------------------------------
    def __enter__(self) -> BaseDriver:
        self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.disconnect()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        state = "connected" if self._connected else "disconnected"
        return f"<{type(self).__name__} {self.name} ({state})>"

"""Layer 3 (HAL) — the DUT driver, over binary UDP.

Satisfies the same ``DeviceUnderTest`` Protocol as the in-process simulated
DUT. Layer 2 cannot tell them apart; only this file knows there is a datagram
socket, a retry counter and a message-id echo check involved.

UDP has no delivery guarantee, no ordering guarantee and no connection, which
means three things a TCP driver never has to think about:

* **Retries belong to the driver.** A lost request is normal, not exceptional.
* **A late reply is a trap.** If request A times out, is retried, and A's
  original answer then arrives while the caller is waiting for B, a naive
  ``recv()`` hands B's caller A's data — a wrong measurement that never raises.
  So every reply is checked against the message id that was asked for, and
  anything else is discarded and waited past.
* **A refusal is not a timeout.** A NACK means the device heard and declined;
  retrying it just wastes the timeout budget. It is raised immediately.
"""

from __future__ import annotations

import socket
import time

from . import dut_protocol as proto
from .base_driver import BaseDriver


class DutTimeout(TimeoutError):
    """The device did not answer within the retry budget."""


class BinaryUdpDut(BaseDriver):
    #: plenty for the largest reply this protocol defines
    RECV_SIZE = 4096

    def __init__(
        self,
        host: str,
        port: int = 50000,
        timeout_s: float = 2.0,
        retries: int = 3,
        name: str = "UDP-DUT",
    ) -> None:
        super().__init__(name)
        self.host = host
        self.port = int(port)
        self.timeout_s = float(timeout_s)
        self.retries = max(1, int(retries))
        self._sock: socket.socket | None = None
        self._ident: proto.Ident | None = None

    # --- lifecycle -------------------------------------------------------
    def connect(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # connect() on a UDP socket does not handshake; it fixes the peer so the
        # kernel drops datagrams from anyone else. Cheap protection on a shared
        # bench network where several tools broadcast.
        sock.connect((self.host, self.port))
        sock.settimeout(self.timeout_s)
        self._sock = sock
        self._connected = True
        try:
            # Prove the device is actually answering now, rather than finding
            # out halfway through a scenario.
            self._ident = self._read_ident()
        except Exception:
            self.disconnect()
            raise

    def disconnect(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None
        self._ident = None
        super().disconnect()

    def _require_socket(self) -> socket.socket:
        self._require_connection()
        if self._sock is None:
            raise RuntimeError(f"{self.name}: socket is not open")
        return self._sock

    # --- transaction -----------------------------------------------------
    def _transact(self, request: bytes, expect: int) -> proto.Frame:
        """Send a request and return the matching reply frame.

        Retries on silence; raises on refusal; ignores anything that is not the
        answer to the question actually asked.
        """
        sock = self._require_socket()
        sent_msg_id = proto.decode(request).msg_id
        last_error: Exception | None = None

        for _attempt in range(self.retries):
            sock.send(request)
            deadline = time.monotonic() + self.timeout_s
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                sock.settimeout(remaining)
                try:
                    data = sock.recv(self.RECV_SIZE)
                except TimeoutError:
                    break
                try:
                    frame = proto.decode(data)
                except proto.ProtocolError as exc:
                    # Garbage or a foreign datagram. Not fatal — keep listening
                    # until the deadline rather than failing the whole request.
                    last_error = exc
                    continue
                if frame.msg_id == proto.RSP_NACK:
                    echoed, reason = proto.decode_nack(frame.payload)
                    if echoed == sent_msg_id:
                        raise proto.NackError(echoed, reason)
                    continue  # a NACK for someone else's request
                if frame.msg_id == expect:
                    return frame
                # A stale reply to an earlier, retried request. Discard it.

        raise DutTimeout(
            f"{self.name}: no reply to message 0x{sent_msg_id:02X} from "
            f"{self.host}:{self.port} after {self.retries} attempt(s) "
            f"of {self.timeout_s:g}s"
            + (f" (last decode error: {last_error})" if last_error else "")
        )

    # --- operations ------------------------------------------------------
    def _read_ident(self) -> proto.Ident:
        frame = self._transact(proto.encode(proto.REQ_IDENT), proto.RSP_IDENT)
        return proto.decode_ident_response(frame.payload)

    def identify(self) -> str:
        ident = self._ident or self._read_ident()
        return f"{ident.name} fw {ident.firmware} sn {ident.serial}"

    def get_relay_states(self) -> dict[str, bool]:
        frame = self._transact(proto.encode(proto.REQ_RELAYS), proto.RSP_RELAYS)
        return proto.decode_relay_response(frame.payload)

    def read_analog_output(self, channel: int) -> float:
        frame = self._transact(proto.encode_analog_request(channel), proto.RSP_ANALOG)
        echoed_channel, value = proto.decode_analog_response(frame.payload)
        if echoed_channel != channel:
            raise proto.ProtocolError(
                f"{self.name}: asked for channel {channel}, device answered for {echoed_channel}"
            )
        return value

    def apply_analog_correction(self, factor: float) -> None:
        frame = self._transact(proto.encode_correction_request(factor), proto.RSP_ACK)
        echoed = proto.decode_ack(frame.payload)
        if echoed != proto.REQ_SET_CORRECTION:
            raise proto.ProtocolError(
                f"{self.name}: ACK echoed 0x{echoed:02X}, expected 0x{proto.REQ_SET_CORRECTION:02X}"
            )

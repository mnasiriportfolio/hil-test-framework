"""A device-under-test emulator, served over UDP with the binary protocol.

The counterpart to :mod:`hiltf.layer3_hal.dut_driver`, backed by the same
:class:`~hiltf.layer3_hal.simulation.SimulatedBench` the SCPI emulator serves —
so the relay states the DUT reports are caused by the stimulus the generator
was told to apply, across two different protocols and two different sockets.

It also refuses things, on purpose. A device that only ever succeeds cannot
test a driver's error path, and the error path is where drivers are wrong:
a bad channel gets ``NACK_BAD_CHANNEL``, a nonsensical calibration factor gets
``NACK_BAD_VALUE``, and an unparseable payload gets ``NACK_BAD_LENGTH``.

``drop_first`` exists for the same reason. UDP loses datagrams; the driver
retries. Setting ``drop_first=2`` makes the emulator swallow the first two
requests so that the retry path runs for real in a test, rather than being
assumed to work because nothing ever went wrong.
"""

from __future__ import annotations

import socketserver
import threading

from ..layer3_hal import dut_protocol as proto
from ..layer3_hal.simulation import SimulatedBench

#: analog output channels this device exposes
VALID_CHANNELS = (1, 2, 3, 4)

DEFAULT_IDENT = proto.Ident(fw_major=2, fw_minor=4, serial=100_247, name="HILTF-SIM-DUT")


class DutLogic:
    """Request frame in, response frame out. No sockets — directly testable."""

    def __init__(self, bench: SimulatedBench, ident: proto.Ident = DEFAULT_IDENT) -> None:
        self.bench = bench
        self.ident = ident

    def execute(self, frame: proto.Frame) -> bytes:
        if frame.msg_id == proto.REQ_IDENT:
            return proto.encode_ident_response(self.ident)

        if frame.msg_id == proto.REQ_RELAYS:
            return proto.encode_relay_response(self.bench.relay_states())

        if frame.msg_id == proto.REQ_ANALOG:
            try:
                channel = proto.decode_analog_request(frame.payload)
            except proto.ProtocolError:
                return proto.encode_nack(frame.msg_id, proto.NACK_BAD_LENGTH)
            if channel not in VALID_CHANNELS:
                return proto.encode_nack(frame.msg_id, proto.NACK_BAD_CHANNEL)
            return proto.encode_analog_response(channel, self.bench.read_analog_output(channel))

        if frame.msg_id == proto.REQ_SET_CORRECTION:
            try:
                factor = proto.decode_correction_request(frame.payload)
            except proto.ProtocolError:
                return proto.encode_nack(frame.msg_id, proto.NACK_BAD_LENGTH)
            # A non-positive or wild correction factor would silently corrupt
            # every later reading, so the device declines it.
            if not (0.1 <= factor <= 10.0):
                return proto.encode_nack(frame.msg_id, proto.NACK_BAD_VALUE)
            self.bench.analog_correction = factor
            return proto.encode_ack(frame.msg_id)

        return proto.encode_nack(frame.msg_id, proto.NACK_UNKNOWN_MESSAGE)


class _DutHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        data, sock = self.request
        server: DutServer = self.server  # type: ignore[assignment]

        if server.should_drop():
            return  # simulate a lost datagram: no reply at all

        try:
            frame = proto.decode(data)
        except proto.ProtocolError:
            # A real device ignores traffic that is not its protocol rather
            # than replying to it. Answering would make the driver's protocol
            # mark check untestable.
            return

        try:
            sock.sendto(server.logic.execute(frame), self.client_address)
        except OSError:
            pass


class DutServer(socketserver.ThreadingUDPServer):
    """Threaded UDP server exposing one :class:`SimulatedBench` as a DUT."""

    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        bench: SimulatedBench,
        host: str = "127.0.0.1",
        port: int = 50000,
        ident: proto.Ident = DEFAULT_IDENT,
        drop_first: int = 0,
    ) -> None:
        self.logic = DutLogic(bench, ident)
        self.bench = bench
        self._drop_remaining = int(drop_first)
        self._lock = threading.Lock()
        super().__init__((host, port), _DutHandler)

    @property
    def port(self) -> int:
        return int(self.server_address[1])

    def should_drop(self) -> bool:
        """Consume one of the configured artificial packet losses."""
        with self._lock:
            if self._drop_remaining > 0:
                self._drop_remaining -= 1
                return True
        return False

    #: see ScpiServer.POLL_INTERVAL_S — shutdown() costs one poll interval
    POLL_INTERVAL_S = 0.02

    def start_background(self) -> threading.Thread:
        thread = threading.Thread(
            target=self.serve_forever,
            args=(self.POLL_INTERVAL_S,),
            name="dut-server",
            daemon=True,
        )
        thread.start()
        return thread

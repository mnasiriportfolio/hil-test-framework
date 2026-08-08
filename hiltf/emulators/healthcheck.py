"""Readiness probe for the containerised bench emulator.

    python -m hiltf.emulators.healthcheck

Used as the ``bench`` service's Docker ``HEALTHCHECK`` so that ``runner``
starts only once the emulator is genuinely answering. ``depends_on`` alone
would only wait for the *container* to start, and a test suite that connects
half a second too early fails in a way that looks like a driver bug.

It checks both protocols, because they are two separate listeners in one
process and either could be the one that failed to bind.
"""
from __future__ import annotations

import argparse
import os
import socket
import sys

from ..layer3_hal import dut_protocol as proto
from ..layer3_hal import scpi_commands as scpi


def check_scpi(host: str, port: int, timeout_s: float = 2.0) -> str:
    """``*IDN?`` over TCP. Returns the identity string or raises."""
    with socket.create_connection((host, port), timeout_s) as sock:
        sock.settimeout(timeout_s)
        sock.sendall((scpi.idn() + scpi.TERMINATOR).encode("ascii"))
        buf = bytearray()
        term = scpi.TERMINATOR.encode("ascii")
        while term not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                raise ConnectionError("SCPI server closed the connection during *IDN?")
            buf.extend(chunk)
        return bytes(buf).partition(term)[0].decode("ascii").strip()


def check_dut(host: str, port: int, timeout_s: float = 2.0) -> str:
    """Identity request over UDP. Returns the device name or raises."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout_s)
        sock.connect((host, port))
        sock.send(proto.encode(proto.REQ_IDENT))
        frame = proto.decode(sock.recv(4096))
        if frame.msg_id != proto.RSP_IDENT:
            raise ConnectionError(f"DUT answered 0x{frame.msg_id:02X}, expected an identity")
        ident = proto.decode_ident_response(frame.payload)
        return f"{ident.name} fw {ident.firmware}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe the bench emulator.")
    parser.add_argument("--host", default=os.environ.get("HILTF_BENCH_HOST", "127.0.0.1"))
    parser.add_argument(
        "--scpi-port", type=int, default=int(os.environ.get("HILTF_SCPI_PORT", 5025))
    )
    parser.add_argument(
        "--dut-port", type=int, default=int(os.environ.get("HILTF_DUT_PORT", 50000))
    )
    parser.add_argument("--timeout", type=float, default=2.0)
    args = parser.parse_args(argv)

    try:
        idn = check_scpi(args.host, args.scpi_port, args.timeout)
        dut = check_dut(args.host, args.dut_port, args.timeout)
    except Exception as exc:  # noqa: BLE001 - a probe reports, it does not raise
        print(f"UNHEALTHY: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 1

    print(f"OK  scpi={idn}  dut={dut}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

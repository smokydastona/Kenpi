"""Connect-only TCP reachability profiler for bench discovery.

This is a developer tool, never imported by the runtime service. It completes a
TCP handshake and closes immediately: no payload is sent, no banner is read, and
only private/loopback/link-local IP literals are accepted. The default ports are
unverified hypotheses from other 70mai models (E1 at most); an open port proves
nothing about the A810 Lite protocol.
"""

from __future__ import annotations

import argparse
import asyncio
import errno
import ipaddress
import json
import socket
import sys
from dataclasses import asdict, dataclass

CANDIDATE_PORTS: dict[int, str] = {
    21: "ftp (hypothesis)",
    23: "telnet (hypothesis)",
    80: "http (hypothesis)",
    554: "rtsp (hypothesis)",
}
MAX_PORTS = 16


@dataclass(frozen=True, slots=True)
class PortResult:
    port: int
    hypothesis: str
    state: str  # open, closed, filtered, or error


def validate_target(host: str) -> str:
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError("target must be an IP address literal") from exc
    if address.is_global or address.is_multicast or address.is_unspecified:
        raise ValueError("target must be a private, loopback or link-local address")
    if not (address.is_private or address.is_loopback or address.is_link_local):
        raise ValueError("target must be a private, loopback or link-local address")
    return str(address)


async def probe_port(host: str, port: int, timeout_seconds: float) -> PortResult:
    hypothesis = CANDIDATE_PORTS.get(port, "unlisted")
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.setblocking(False)
    loop = asyncio.get_running_loop()
    try:
        await asyncio.wait_for(loop.sock_connect(sock, (host, port)), timeout_seconds)
        state = "open"
    except asyncio.TimeoutError:
        state = "filtered"
    except ConnectionRefusedError:
        state = "closed"
    except OSError as exc:
        state = (
            "filtered"
            if exc.errno in (errno.EHOSTUNREACH, errno.ENETUNREACH)
            else "error"
        )
    finally:
        sock.close()
    return PortResult(port, hypothesis, state)


async def profile(
    host: str,
    ports: tuple[int, ...] = tuple(CANDIDATE_PORTS),
    timeout_seconds: float = 2.0,
) -> dict[str, object]:
    host = validate_target(host)
    if not 0.1 <= timeout_seconds <= 10:
        raise ValueError("timeout must be between 0.1 and 10 seconds")
    unique = tuple(dict.fromkeys(ports))
    if not unique or len(unique) > MAX_PORTS:
        raise ValueError(f"between 1 and {MAX_PORTS} ports are required")
    if any(type(port) is not int or not 1 <= port <= 65535 for port in unique):
        raise ValueError("ports must be between 1 and 65535")
    results = await asyncio.gather(
        *(probe_port(host, port, timeout_seconds) for port in unique)
    )
    return {
        "schema": "kenpi.profile/1",
        "evidence": "E0",
        "method": "tcp-connect-only",
        "target": host,
        "ports": [asdict(result) for result in results],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Connect-only TCP reachability profile"
    )
    parser.add_argument("--target", required=True, help="private IP address literal")
    parser.add_argument("--port", type=int, action="append", help="repeatable")
    parser.add_argument("--timeout", type=float, default=2.0)
    arguments = parser.parse_args(argv)
    ports = tuple(arguments.port) if arguments.port else tuple(CANDIDATE_PORTS)
    try:
        report = asyncio.run(profile(arguments.target, ports, arguments.timeout))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

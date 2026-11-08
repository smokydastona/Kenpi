"""K-Line session layer: 5-baud and fast initialisation, request/response timing.

Written from the public ISO 9141-2 and ISO 14230-2 timing descriptions, not
copied from any GPL project. It talks to an abstract `KLinePort`; the real port
(UART plus an automotive K-Line transceiver such as an L9637D, pins unassigned)
does not exist yet. Only the read-only requests from `kline.encode_request` can
be sent. Tested against a simulated ECU only: it has never talked to a real ECU,
and the timing values below must be checked against one on a bench.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Protocol

from kenpi.core.errors import ProtocolError
from kenpi.vehicle.kline import (
    TESTER_ADDRESS,
    Response,
    Style,
    checksum,
    decode_response,
    encode_request,
)

BAUD = 10400
OBD_ADDRESS = 0x33
SYNC_BYTE = 0x55
FAST_INIT_LOW_S = 0.025
FAST_INIT_HIGH_S = 0.025
SYNC_TIMEOUT_S = 0.300  # ECU sync after the 5-baud address (W1 max)
KEYWORD_TIMEOUT_S = 0.020  # W2 max
ADDRESS_ECHO_TIMEOUT_S = 0.050  # W4 max
RESPONSE_TIMEOUT_S = 0.050  # P2 max for a normal response
P3_MIN_S = 0.055  # quiet time between a response and the next request
BYTE_GAP_S = 0.005  # P4 min between tester bytes

# StartCommunication (ISO 14230-2): format, target, source, service, checksum.
START_COMMUNICATION = bytes([0xC1, OBD_ADDRESS, TESTER_ADDRESS, 0x81, 0x66])


class KLinePort(Protocol):
    def set_baud(self, baud: int) -> None: ...
    def write(self, data: bytes) -> None: ...
    def read(self, count: int, timeout: float) -> bytes:
        """Return up to `count` bytes; fewer (or none) if the timeout expires."""

    def drain(self) -> None:
        """Discard anything received (K-Line echoes the tester's own bytes)."""

    def send_five_baud(self, byte: int) -> None:
        """Send one byte at 5 baud by driving the line directly."""

    def pulse_low(self, seconds: float) -> None:
        """Hold the line low, then release it."""


class InitError(ProtocolError):
    """The ECU did not complete initialisation."""


@dataclass(frozen=True, slots=True)
class InitResult:
    style: Style
    keywords: tuple[int, int]
    fast: bool


def classify_keywords(kb1: int, kb2: int) -> Style:
    """08 08 (some ECUs 94 94) means ISO 9141-2; other keywords with the high bit set are ISO 14230."""
    if (kb1, kb2) in {(0x08, 0x08), (0x94, 0x94)}:
        return Style.ISO_9141_2
    if kb1 & 0x80 or kb2 & 0x80:
        return Style.ISO_14230
    raise InitError(f"unrecognised keywords {kb1:02X} {kb2:02X}")


class KLineSession:
    def __init__(
        self,
        port: KLinePort,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._port = port
        self._clock = clock
        self._sleep = sleep
        self.style: Style | None = None
        self._last_rx = 0.0

    def _expect(self, count: int, timeout: float, what: str) -> bytes:
        data = self._port.read(count, timeout)
        if len(data) != count:
            raise InitError(f"timeout waiting for {what}")
        return data

    def init_five_baud(self) -> InitResult:
        port = self._port
        port.set_baud(BAUD)
        port.drain()
        port.send_five_baud(OBD_ADDRESS)
        port.drain()
        if self._expect(1, SYNC_TIMEOUT_S, "sync byte")[0] != SYNC_BYTE:
            raise InitError("unexpected sync byte")
        kb1, kb2 = self._expect(2, KEYWORD_TIMEOUT_S * 2, "keywords")
        self._sleep(BYTE_GAP_S * 5)
        port.write(bytes([~kb2 & 0xFF]))
        port.drain()
        reply = self._expect(1, ADDRESS_ECHO_TIMEOUT_S * 2, "address complement")
        if reply[0] != (~OBD_ADDRESS & 0xFF):
            raise InitError("bad address complement")
        self.style = classify_keywords(kb1, kb2)
        self._last_rx = self._clock()
        return InitResult(self.style, (kb1, kb2), fast=False)

    def init_fast(self) -> InitResult:
        port = self._port
        port.set_baud(BAUD)
        port.drain()
        port.pulse_low(FAST_INIT_LOW_S)
        self._sleep(FAST_INIT_HIGH_S)
        port.write(START_COMMUNICATION)
        port.drain()
        raw = self._port.read(7, RESPONSE_TIMEOUT_S * 4)
        if len(raw) < 7:
            raise InitError("no StartCommunication response")
        # format, target, source, 0xC1, KB1, KB2, checksum
        if checksum(raw[:-1]) != raw[-1] or raw[3] != 0xC1:
            raise InitError("bad StartCommunication response")
        self.style = Style.ISO_14230
        self._last_rx = self._clock()
        return InitResult(Style.ISO_14230, (raw[4], raw[5]), fast=True)

    def request(self, service: int, pid: int | None = None) -> Response:
        if self.style is None:
            raise ProtocolError("K-Line session is not initialised")
        frame = encode_request(self.style, service, pid)
        wait = P3_MIN_S - (self._clock() - self._last_rx)
        if wait > 0:
            self._sleep(wait)
        self._port.drain()
        for byte in frame:
            self._port.write(bytes([byte]))
            self._sleep(BYTE_GAP_S)
        self._port.drain()
        reply = self._read_frame()
        self._last_rx = self._clock()
        return decode_response(self.style, reply)

    def _read_frame(self) -> bytes:
        assert self.style is not None
        first = self._port.read(1, RESPONSE_TIMEOUT_S)
        if not first:
            raise ProtocolError("timeout waiting for ECU response")
        if self.style is Style.ISO_14230:
            length = first[0] & 0x3F
            head = first + self._need(2 if length else 3)
            if length == 0:
                length = head[-1]
            return head + self._need(length + 1)
        # ISO 9141-2 has no length field: read until the line goes idle.
        buf = bytearray(first)
        while len(buf) < 12:
            more = self._port.read(1, 0.02)
            if not more:
                break
            buf += more
        return bytes(buf)

    def _need(self, count: int) -> bytes:
        data = self._port.read(count, RESPONSE_TIMEOUT_S)
        if len(data) != count:
            raise ProtocolError("truncated ECU frame")
        return data

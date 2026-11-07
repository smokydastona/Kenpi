"""Read-only K-Line (ISO 9141-2 / ISO 14230) OBD-II frame codec.

Pure byte handling, no transport. It encodes only the read-only SAE J1979
services (01 current data, 02 freeze frame, 03 stored trouble codes, 09
vehicle info) and refuses everything else, so nothing built on it can send
actuator, write or programming requests. Host tests only: it has not been run
against any vehicle or ECU (see docs/vehicle-diagnostics-research.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from kenpi.core.errors import ProtocolError

READ_ONLY_SERVICES = frozenset({0x01, 0x02, 0x03, 0x09})
TESTER_ADDRESS = 0xF1
MAX_DATA = 63


class Style(Enum):
    ISO_9141_2 = "iso9141-2"
    ISO_14230 = "iso14230"


def checksum(data: bytes) -> int:
    return sum(data) & 0xFF


def encode_request(style: Style, service: int, pid: int | None = None) -> bytes:
    if service not in READ_ONLY_SERVICES:
        raise ValueError(
            f"service 0x{service:02X} is not a permitted read-only service"
        )
    data = bytes([service]) if pid is None else bytes([service, pid])
    if pid is not None and not 0 <= pid <= 0xFF:
        raise ValueError("pid must fit in one byte")
    if style is Style.ISO_9141_2:
        frame = bytes([0x68, 0x6A, TESTER_ADDRESS]) + data
    else:
        frame = bytes([0xC0 | len(data), 0x33, TESTER_ADDRESS]) + data
    return frame + bytes([checksum(frame)])


@dataclass(frozen=True, slots=True)
class Response:
    source: int
    data: bytes

    @property
    def service(self) -> int:
        """Request service this answers (response SID minus 0x40)."""
        return self.data[0] - 0x40


def decode_response(style: Style, frame: bytes) -> Response:
    if len(frame) < 5:
        raise ProtocolError("K-Line frame too short")
    if checksum(frame[:-1]) != frame[-1]:
        raise ProtocolError("K-Line checksum mismatch")
    if style is Style.ISO_9141_2:
        data = frame[3:-1]
    else:
        length = frame[0] & 0x3F
        offset = 3
        if length == 0:
            length, offset = frame[3], 4
        data = frame[offset:-1]
        if len(data) != length:
            raise ProtocolError("K-Line length does not match frame")
    if not data:
        raise ProtocolError("K-Line frame has no data")
    if data[0] == 0x7F:
        code = data[2] if len(data) > 2 else None
        raise ProtocolError(f"ECU negative response (code {code})")
    return Response(source=frame[2], data=bytes(data))


def decode_pid(response: Response) -> tuple[str, float, str]:
    """Decode the standard SAE J1979 PIDs Kenpi understands: (name, value, unit)."""
    data = response.data
    if response.service != 0x01 or len(data) < 3:
        raise ProtocolError("not a mode 01 response")
    pid, values = data[1], data[2:]
    if pid == 0x05 and len(values) >= 1:
        return "coolant_temperature", values[0] - 40.0, "C"
    if pid == 0x0C and len(values) >= 2:
        return "engine_speed", (values[0] * 256 + values[1]) / 4.0, "rpm"
    if pid == 0x0D and len(values) >= 1:
        return "vehicle_speed", float(values[0]), "km/h"
    raise ProtocolError(f"PID 0x{pid:02X} is not decoded")

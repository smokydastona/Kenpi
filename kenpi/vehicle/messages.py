"""Typed vehicle-link messages carried in `kenpi.vehicle.protocol` frames.

The schema is owned by Kenpi on both ends (Pi and auxiliary controller); it is
mirrored byte-for-byte in `firmware/include/kenpi/messages.h` and checked by the
shared fixtures in `protocol/vehicle_frames.txt`. Multi-byte fields are
big-endian. No message commands dashcam power.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from kenpi.core.errors import ProtocolError
from kenpi.vehicle.kline import READ_ONLY_SERVICES
from kenpi.vehicle.protocol import VehicleMessage


class MessageType(IntEnum):
    HEARTBEAT = 0x01  # controller -> Pi, u32 uptime_ms
    POWER_STATE = 0x02  # controller -> Pi, u8 PowerState, u8 flags
    SHUTDOWN_REQUEST = 0x03  # controller -> Pi, u16 grace_seconds
    SHUTDOWN_ACK = 0x04  # Pi -> controller, empty
    DIAG_REQUEST = 0x05  # Pi -> controller, u16 request_id, u8 service[, u8 pid]
    DIAG_RESPONSE = 0x06  # controller -> Pi, u16 request_id, u8 status, data


class PowerState(IntEnum):
    """Order matches `kenpi::PowerState` in the firmware."""

    VEHICLE_OFF = 0
    IGNITION_DETECTED = 1
    STARTING = 2
    RUNNING = 3
    IGNITION_LOST = 4
    SHUTDOWN_PENDING = 5
    SHUTTING_DOWN = 6
    SLEEPING = 7
    WAKING = 8
    FAULT = 9


FLAG_IGNITION_ON = 0x01
_KNOWN_FLAGS = FLAG_IGNITION_ON


@dataclass(frozen=True, slots=True)
class Heartbeat:
    uptime_ms: int


@dataclass(frozen=True, slots=True)
class PowerStateReport:
    state: PowerState
    ignition_on: bool


@dataclass(frozen=True, slots=True)
class ShutdownRequest:
    grace_seconds: int


@dataclass(frozen=True, slots=True)
class ShutdownAck:
    pass


class DiagnosticStatus(IntEnum):
    OK = 0
    TIMEOUT = 1
    NEGATIVE_RESPONSE = 2
    BUS_UNAVAILABLE = 3
    REJECTED = 4


MAX_DIAG_DATA = 32
_PID_SERVICES = frozenset({0x01, 0x02, 0x09})


@dataclass(frozen=True, slots=True)
class DiagnosticRequest:
    """Read-only OBD-II request relayed to the vehicle bus by the controller.

    Only services 01, 02, 03 and 09 are representable; the controller must
    refuse anything else as well.
    """

    request_id: int
    service: int
    pid: int | None = None

    def __post_init__(self) -> None:
        if not 0 <= self.request_id <= 0xFFFF:
            raise ProtocolError("request_id must fit in two bytes")
        if self.service not in READ_ONLY_SERVICES:
            raise ProtocolError(f"service 0x{self.service:02X} is not read-only")
        if self.service in _PID_SERVICES:
            if self.pid is None or not 0 <= self.pid <= 0xFF:
                raise ProtocolError("this service needs a one-byte pid")
        elif self.pid is not None:
            raise ProtocolError("this service takes no pid")


@dataclass(frozen=True, slots=True)
class DiagnosticResponse:
    request_id: int
    status: DiagnosticStatus
    data: bytes = b""

    def __post_init__(self) -> None:
        if not 0 <= self.request_id <= 0xFFFF:
            raise ProtocolError("request_id must fit in two bytes")
        if len(self.data) > MAX_DIAG_DATA:
            raise ProtocolError("diagnostic data too long")
        if self.data and self.status not in (
            DiagnosticStatus.OK,
            DiagnosticStatus.NEGATIVE_RESPONSE,
        ):
            raise ProtocolError("only OK and negative responses carry data")
        if self.status is DiagnosticStatus.OK and not self.data:
            raise ProtocolError("an OK diagnostic response must carry data")


TypedMessage = (
    Heartbeat
    | PowerStateReport
    | ShutdownRequest
    | ShutdownAck
    | DiagnosticRequest
    | DiagnosticResponse
)


def to_frame_message(message: TypedMessage, sequence: int) -> VehicleMessage:
    if isinstance(message, Heartbeat):
        if not 0 <= message.uptime_ms <= 0xFFFFFFFF:
            raise ProtocolError("uptime_ms must fit in four bytes")
        return VehicleMessage(
            MessageType.HEARTBEAT, sequence, message.uptime_ms.to_bytes(4, "big")
        )
    if isinstance(message, PowerStateReport):
        flags = FLAG_IGNITION_ON if message.ignition_on else 0
        return VehicleMessage(
            MessageType.POWER_STATE,
            sequence,
            bytes((int(PowerState(message.state)), flags)),
        )
    if isinstance(message, ShutdownRequest):
        if not 0 <= message.grace_seconds <= 0xFFFF:
            raise ProtocolError("grace_seconds must fit in two bytes")
        return VehicleMessage(
            MessageType.SHUTDOWN_REQUEST,
            sequence,
            message.grace_seconds.to_bytes(2, "big"),
        )
    if isinstance(message, ShutdownAck):
        return VehicleMessage(MessageType.SHUTDOWN_ACK, sequence)
    if isinstance(message, DiagnosticRequest):
        body = message.request_id.to_bytes(2, "big") + bytes([message.service])
        if message.pid is not None:
            body += bytes([message.pid])
        return VehicleMessage(MessageType.DIAG_REQUEST, sequence, body)
    if isinstance(message, DiagnosticResponse):
        return VehicleMessage(
            MessageType.DIAG_RESPONSE,
            sequence,
            message.request_id.to_bytes(2, "big")
            + bytes([int(message.status)])
            + message.data,
        )
    raise ProtocolError("unsupported message object")


def from_frame_message(frame: VehicleMessage) -> TypedMessage:
    payload = frame.payload
    if frame.message_type == MessageType.HEARTBEAT and len(payload) == 4:
        return Heartbeat(int.from_bytes(payload, "big"))
    if frame.message_type == MessageType.POWER_STATE and len(payload) == 2:
        if payload[0] not in set(int(state) for state in PowerState):
            raise ProtocolError("unknown power state")
        if payload[1] & ~_KNOWN_FLAGS:
            raise ProtocolError("unknown power state flags")
        return PowerStateReport(
            PowerState(payload[0]), bool(payload[1] & FLAG_IGNITION_ON)
        )
    if frame.message_type == MessageType.SHUTDOWN_REQUEST and len(payload) == 2:
        return ShutdownRequest(int.from_bytes(payload, "big"))
    if frame.message_type == MessageType.SHUTDOWN_ACK and not payload:
        return ShutdownAck()
    if frame.message_type == MessageType.DIAG_REQUEST and len(payload) in (3, 4):
        return DiagnosticRequest(
            int.from_bytes(payload[:2], "big"),
            payload[2],
            payload[3] if len(payload) == 4 else None,
        )
    if frame.message_type == MessageType.DIAG_RESPONSE and len(payload) >= 3:
        if payload[2] not in set(int(x) for x in DiagnosticStatus):
            raise ProtocolError("unknown diagnostic status")
        return DiagnosticResponse(
            int.from_bytes(payload[:2], "big"),
            DiagnosticStatus(payload[2]),
            bytes(payload[3:]),
        )
    raise ProtocolError(
        f"unknown message type {frame.message_type} or invalid payload length"
    )

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
from kenpi.vehicle.protocol import VehicleMessage


class MessageType(IntEnum):
    HEARTBEAT = 0x01  # controller -> Pi, u32 uptime_ms
    POWER_STATE = 0x02  # controller -> Pi, u8 PowerState, u8 flags
    SHUTDOWN_REQUEST = 0x03  # controller -> Pi, u16 grace_seconds
    SHUTDOWN_ACK = 0x04  # Pi -> controller, empty


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


TypedMessage = Heartbeat | PowerStateReport | ShutdownRequest | ShutdownAck


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
    raise ProtocolError(
        f"unknown message type {frame.message_type} or invalid payload length"
    )

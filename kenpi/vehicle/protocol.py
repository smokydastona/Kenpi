"""Framed UART protocol primitives for the independent auxiliary controller."""

from __future__ import annotations

from dataclasses import dataclass

from kenpi.core.errors import ProtocolError

MAGIC = b"KP"
PROTOCOL_VERSION = 1
MAX_PAYLOAD_LENGTH = 512
_HEADER_SIZE = 8
_CRC_SIZE = 2


def _crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


@dataclass(frozen=True, slots=True)
class VehicleMessage:
    message_type: int
    sequence: int
    payload: bytes = b""
    version: int = PROTOCOL_VERSION

    def __post_init__(self) -> None:
        if not 0 <= self.message_type <= 255:
            raise ValueError("message_type must fit in one byte")
        if not 0 <= self.sequence <= 0xFFFF:
            raise ValueError("sequence must fit in two bytes")
        if self.version != PROTOCOL_VERSION:
            raise ValueError("unsupported protocol version")
        if len(self.payload) > MAX_PAYLOAD_LENGTH:
            raise ValueError("payload exceeds protocol limit")


def encode_message(message: VehicleMessage) -> bytes:
    header = (
        MAGIC
        + bytes((message.version, message.message_type))
        + message.sequence.to_bytes(2, "big")
        + len(message.payload).to_bytes(2, "big")
    )
    body = header + message.payload
    return body + _crc16(body).to_bytes(2, "big")


class VehicleMessageDecoder:
    """Incremental decoder tolerating partial input and resynchronizing on magic."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, chunk: bytes) -> tuple[VehicleMessage, ...]:
        self._buffer.extend(chunk)
        messages: list[VehicleMessage] = []
        while True:
            start = self._buffer.find(MAGIC)
            if start < 0:
                self._buffer[:] = self._buffer[-1:] if self._buffer[-1:] == MAGIC[:1] else b""
                break
            if start:
                del self._buffer[:start]
            if len(self._buffer) < _HEADER_SIZE:
                break
            version, message_type = self._buffer[2], self._buffer[3]
            sequence = int.from_bytes(self._buffer[4:6], "big")
            payload_length = int.from_bytes(self._buffer[6:8], "big")
            if payload_length > MAX_PAYLOAD_LENGTH:
                del self._buffer[0]
                continue
            frame_length = _HEADER_SIZE + payload_length + _CRC_SIZE
            if len(self._buffer) < frame_length:
                break
            frame = bytes(self._buffer[:frame_length])
            expected_crc = int.from_bytes(frame[-_CRC_SIZE:], "big")
            if _crc16(frame[:-_CRC_SIZE]) != expected_crc:
                del self._buffer[0]
                continue
            del self._buffer[:frame_length]
            if version != PROTOCOL_VERSION:
                continue
            messages.append(VehicleMessage(message_type, sequence, frame[_HEADER_SIZE:-_CRC_SIZE], version))
        return tuple(messages)


class SequenceTracker:
    """Reject duplicate or stale sequence numbers using modular ordering."""

    def __init__(self) -> None:
        self._last: int | None = None

    def accept(self, sequence: int) -> bool:
        if not 0 <= sequence <= 0xFFFF:
            raise ProtocolError("sequence is outside the protocol range")
        if self._last is None:
            self._last = sequence
            return True
        delta = (sequence - self._last) & 0xFFFF
        if delta == 0 or delta >= 0x8000:
            return False
        self._last = sequence
        return True

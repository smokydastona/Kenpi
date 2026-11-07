"""Minimal RFC 6455 server-side framing (text frames, ping/pong, close)."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import struct

GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
OP_CONT, OP_TEXT, OP_BINARY, OP_CLOSE, OP_PING, OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA
MAX_PAYLOAD = 16 * 1024


class WebSocketError(Exception):
    def __init__(self, code: int, reason: str) -> None:
        super().__init__(reason)
        self.code, self.reason = code, reason


def accept_key(client_key: str) -> str:
    return base64.b64encode(hashlib.sha1(client_key.encode() + GUID).digest()).decode()


def encode_frame(opcode: int, payload: bytes = b"") -> bytes:
    length = len(payload)
    if length < 126:
        header = struct.pack("!BB", 0x80 | opcode, length)
    elif length < 65536:
        header = struct.pack("!BBH", 0x80 | opcode, 126, length)
    else:
        header = struct.pack("!BBQ", 0x80 | opcode, 127, length)
    return header + payload


def close_frame(code: int, reason: str = "") -> bytes:
    return encode_frame(OP_CLOSE, struct.pack("!H", code) + reason.encode()[:100])


async def read_frame(reader: asyncio.StreamReader) -> tuple[int, bytes]:
    """Read one unfragmented, masked client frame; raises WebSocketError on violations."""
    head = await reader.readexactly(2)
    fin, rsv = head[0] & 0x80, head[0] & 0x70
    opcode, masked, length = head[0] & 0x0F, head[1] & 0x80, head[1] & 0x7F
    if rsv:
        raise WebSocketError(1002, "reserved bits set")
    if not masked:
        raise WebSocketError(1002, "client frames must be masked")
    if length == 126:
        (length,) = struct.unpack("!H", await reader.readexactly(2))
    elif length == 127:
        (length,) = struct.unpack("!Q", await reader.readexactly(8))
    if length > MAX_PAYLOAD:
        raise WebSocketError(1009, "message too large")
    if opcode >= 0x8 and (length > 125 or not fin):
        raise WebSocketError(1002, "bad control frame")
    if not fin or opcode == OP_CONT:
        raise WebSocketError(1003, "fragmentation not supported")
    mask = await reader.readexactly(4)
    data = bytearray(await reader.readexactly(length))
    for index in range(length):
        data[index] ^= mask[index % 4]
    return opcode, bytes(data)

import asyncio
import base64
import json
import os
import ssl
import struct

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from kenpi.security.devices import (
    COLLECT_CONTEXT,
    REFRESH_CONTEXT,
    b64e,
    enroll_message,
)


class DeviceKey:
    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.public = b64e(
            self.key.public_key().public_bytes(
                serialization.Encoding.DER,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        )

    def sign(self, message: bytes) -> str:
        return b64e(self.key.sign(message, ec.ECDSA(hashes.SHA256())))

    def enroll_proof(self, eid, challenge, name):
        return self.sign(enroll_message(eid, challenge, name, self.public))

    def collect_proof(self, eid):
        return self.sign(COLLECT_CONTEXT + b"\n" + eid.encode())

    def refresh_proof(self, token):
        return self.sign(REFRESH_CONTEXT + b"\n" + token.encode())


def client_context():
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


async def http(port, method, path, body=None, headers=None, ssl_ctx=None):
    reader, writer = await asyncio.open_connection("127.0.0.1", port, ssl=ssl_ctx)
    data = json.dumps(body).encode() if body is not None else b""
    head = f"{method} {path} HTTP/1.1\r\nHost: x\r\nContent-Length: {len(data)}\r\n"
    for key, value in (headers or {}).items():
        head += f"{key}: {value}\r\n"
    writer.write(head.encode() + b"\r\n" + data)
    await writer.drain()
    raw = await reader.read()
    writer.close()
    head_part, _, payload = raw.partition(b"\r\n\r\n")
    status = int(head_part.split(b" ")[1])
    return status, json.loads(payload) if payload else {}


def masked(opcode, payload=b""):
    mask = os.urandom(4)
    body = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    length = len(payload)
    if length < 126:
        header = struct.pack("!BB", 0x80 | opcode, 0x80 | length)
    else:
        header = struct.pack("!BBH", 0x80 | opcode, 0x80 | 126, length)
    return header + mask + body


class WsClient:
    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer

    @classmethod
    async def connect(cls, port, ssl_ctx=None):
        reader, writer = await asyncio.open_connection("127.0.0.1", port, ssl=ssl_ctx)
        key = base64.b64encode(os.urandom(16)).decode()
        writer.write(
            (
                "GET /ws HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\n"
                f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                "Sec-WebSocket-Version: 13\r\n\r\n"
            ).encode()
        )
        head = await reader.readuntil(b"\r\n\r\n")
        return cls(reader, writer), head

    def send_json(self, message):
        self.writer.write(masked(0x1, json.dumps(message).encode()))

    async def recv(self, timeout=5):
        async def read():
            head = await self.reader.readexactly(2)
            length = head[1] & 0x7F
            if length == 126:
                (length,) = struct.unpack("!H", await self.reader.readexactly(2))
            return head[0] & 0x0F, await self.reader.readexactly(length)

        return await asyncio.wait_for(read(), timeout)

    async def recv_json(self, timeout=5):
        while True:
            opcode, data = await self.recv(timeout)
            if opcode == 0x1:
                return json.loads(data)
            if opcode == 0x8:
                return {"type": "closed", "code": struct.unpack("!H", data[:2])[0]}

    def close(self):
        self.writer.close()

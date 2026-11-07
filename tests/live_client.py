"""Reference gateway client used by the live integration tests (run inside network namespaces).

Implements what an Android client must do: pin the gateway certificate fingerprint from the
pairing payload, enrol with a device key, collect credentials once, refresh, and keep a
WebSocket open that reconnects with its last-seen sequence. Each invocation does one job and
prints JSON, so a test harness can drive it from any network namespace.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import ssl
import struct
import subprocess
import sys
import time
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from tests.gateway_helpers import DeviceKey, masked


class PersistentKey(DeviceKey):
    def __init__(self, pem: bytes | None = None):
        super().__init__()
        if pem:
            self.key = serialization.load_pem_private_key(pem, None)
            from kenpi.security.devices import b64e

            self.public = b64e(
                self.key.public_key().public_bytes(
                    serialization.Encoding.DER,
                    serialization.PublicFormat.SubjectPublicKeyInfo,
                )
            )

    def pem(self) -> bytes:
        return self.key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )


assert ec  # imported for the key type used by DeviceKey


class PinMismatch(Exception):
    pass


async def connect(host: str, port: int, fingerprint: str, timeout: float = 8.0):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # trust comes from the pin below, not a CA
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port, ssl=ctx), timeout
    )
    der = writer.get_extra_info("ssl_object").getpeercert(binary_form=True)
    if hashlib.sha256(der).hexdigest() != fingerprint.replace(":", "").lower():
        writer.close()
        raise PinMismatch("gateway certificate does not match the pinned fingerprint")
    return reader, writer


async def request(state, method, path, body=None, token=None, host=None, timeout=8.0):
    reader, writer = await connect(
        host or state["host"], state["port"], state["fingerprint"], timeout
    )
    data = json.dumps(body).encode() if body is not None else b""
    head = f"{method} {path} HTTP/1.1\r\nHost: kenpi\r\nContent-Length: {len(data)}\r\n"
    if token:
        head += f"Authorization: Bearer {token}\r\n"
    writer.write(head.encode() + b"\r\n" + data)
    await writer.drain()
    raw = await asyncio.wait_for(reader.read(), timeout)
    writer.close()
    head_part, _, payload = raw.partition(b"\r\n\r\n")
    return int(head_part.split(b" ")[1]), json.loads(payload) if payload else {}


def load(path):
    return json.loads(Path(path).read_text())


def save(path, state):
    Path(path).write_text(json.dumps(state))
    os.chmod(path, 0o600)


def wg(*args, stdin=None):
    return subprocess.run(
        ["wg", *args], input=stdin, capture_output=True, text=True, check=True
    ).stdout.strip()


def parse_payload(uri):
    encoded = uri.split("d=", 1)[1]
    return json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))


async def cmd_enroll(a):
    p = parse_payload(a.payload)
    key = PersistentKey()
    wg_private = wg("genkey")
    state = {
        "host": a.host or p["address"], "port": p["port"], "fingerprint": p["fingerprint"],
        "eid": p["enrollment_id"], "key": key.pem().decode(), "wg_private": wg_private,
    }  # fmt: skip
    body = {
        "enrollment_id": p["enrollment_id"], "challenge": p["challenge"], "name": a.name,
        "public_key": key.public,
        "signature": key.enroll_proof(p["enrollment_id"], p["challenge"], a.name),
        "wireguard_key": wg("pubkey", stdin=wg_private),
    }  # fmt: skip
    status, reply = await request(state, "POST", "/pair/enroll", body)
    save(a.state, state)
    return {"status": status, **reply}


async def cmd_collect(a):
    state = load(a.state)
    key = PersistentKey(state["key"].encode())
    deadline = time.monotonic() + a.timeout
    while True:
        status, reply = await request(
            state, "POST", "/pair/status",
            {"enrollment_id": state["eid"], "signature": key.collect_proof(state["eid"])},
        )  # fmt: skip
        if (
            reply.get("state") == "approved"
            or time.monotonic() > deadline
            or status != 200
        ):
            break
        await asyncio.sleep(0.5)
    if reply.get("state") == "approved":
        state.update(
            device_id=reply["device_id"], access=reply["access_token"],
            refresh=reply["refresh_token"], access_exp=time.time() + reply["expires_in"],
        )  # fmt: skip
        save(a.state, state)
    return {"status": status, "state": reply.get("state"), "device_id": state.get("device_id"),
            "error": reply.get("error")}  # fmt: skip


async def refresh(state, path):
    key = PersistentKey(state["key"].encode())
    status, reply = await request(
        state, "POST", "/auth/refresh",
        {"refresh_token": state["refresh"], "signature": key.refresh_proof(state["refresh"])},
    )  # fmt: skip
    if status == 200:
        state.update(
            access=reply["access_token"], refresh=reply["refresh_token"],
            access_exp=time.time() + reply["expires_in"],
        )  # fmt: skip
        save(path, state)
    return status, reply


async def token(state, path):
    if time.time() > state.get("access_exp", 0) - 30:
        status, reply = await refresh(state, path)
        if status != 200:
            raise RuntimeError(f"refresh failed: {status} {reply}")
    return state["access"]


async def cmd_refresh(a):
    state = load(a.state)
    status, reply = await refresh(state, a.state)
    return {"status": status, "error": reply.get("error")}


async def cmd_snapshot(a):
    state = load(a.state)
    status, reply = await request(
        state, "GET", "/api/v1/snapshot", token=await token(state, a.state), host=a.host
    )
    return {"status": status, "type": reply.get("type"), "error": reply.get("error"),
            "sequence": reply.get("sequence")}  # fmt: skip


async def cmd_wgconf(a):
    state = load(a.state)
    status, reply = await request(
        state,
        "GET",
        "/api/v1/wireguard",
        token=await token(state, a.state),
        host=a.host,
    )
    if status != 200:
        return {"status": status, "error": reply.get("error")}
    w = reply["wireguard"]
    Path(a.out).write_text(
        "[Interface]\n"
        f"PrivateKey = {state['wg_private']}\nAddress = {w['address']}\n\n"
        "[Peer]\n"
        f"PublicKey = {w['server_public_key']}\nEndpoint = {a.endpoint}:{w['listen_port']}\n"
        f"AllowedIPs = {', '.join(w['allowed_ips'])}\nPersistentKeepalive = 1\n"
    )
    os.chmod(a.out, 0o600)
    return {
        "status": 200,
        "gateway_address": w["gateway_address"],
        "address": w["address"],
    }


async def read_frame(reader):
    head = await reader.readexactly(2)
    length = head[1] & 0x7F
    if length == 126:
        (length,) = struct.unpack("!H", await reader.readexactly(2))
    return head[0] & 0x0F, await reader.readexactly(length)


async def cmd_ws(a):
    """Stay connected for ``--seconds``, reconnecting with last-seen sequence; log each event."""
    state = load(a.state)
    log = open(a.log, "a", buffering=1)  # noqa: SIM115 - long-lived log handle
    last, stream = None, None
    end = time.monotonic() + a.seconds

    def note(**kw):
        log.write(json.dumps({"t": round(time.time(), 2), **kw}) + "\n")

    while time.monotonic() < end:
        try:
            reader, writer = await connect(a.host or state["host"], state["port"], state["fingerprint"], 5)  # fmt: skip
            key = base64.b64encode(os.urandom(16)).decode()
            writer.write(
                (
                    f"GET /ws HTTP/1.1\r\nHost: kenpi\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                    f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
                ).encode()  # fmt: skip
            )
            await reader.readuntil(b"\r\n\r\n")
            hello = {"type": "auth", "token": await token(state, a.state)}
            if last is not None:
                hello.update(last_sequence=last, stream_id=stream)
            writer.write(masked(0x1, json.dumps(hello).encode()))
            note(event="connected", resume=last is not None)
            while time.monotonic() < end:
                opcode, data = await asyncio.wait_for(read_frame(reader), 25)
                if opcode == 0x8:
                    note(event="closed", code=struct.unpack("!H", data[:2])[0])
                    if struct.unpack("!H", data[:2])[0] == 4401:
                        return {"result": "unauthorized"}
                    break
                if opcode == 0x1:
                    msg = json.loads(data)
                    if "sequence" in msg and isinstance(msg["sequence"], int):
                        last, stream = msg["sequence"], msg.get("stream_id", stream)
                    note(
                        event="message",
                        type=msg.get("type"),
                        sequence=msg.get("sequence"),
                    )
        except (
            OSError,
            asyncio.TimeoutError,
            asyncio.IncompleteReadError,
            ssl.SSLError,
            RuntimeError,
        ) as exc:
            note(event="error", error=type(exc).__name__)
        await asyncio.sleep(1)
    return {"result": "done"}


COMMANDS = {
    "enroll": cmd_enroll, "collect": cmd_collect, "refresh": cmd_refresh,
    "snapshot": cmd_snapshot, "wgconf": cmd_wgconf, "ws": cmd_ws,
}  # fmt: skip


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("cmd", choices=COMMANDS)
    parser.add_argument("--state", required=True)
    parser.add_argument("--payload")
    parser.add_argument("--name", default="live-phone")
    parser.add_argument("--host")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--out")
    parser.add_argument("--endpoint")
    parser.add_argument("--seconds", type=float, default=60.0)
    parser.add_argument("--log")
    args = parser.parse_args()
    try:
        result = asyncio.run(COMMANDS[args.cmd](args))
    except (
        OSError,
        asyncio.TimeoutError,
        PinMismatch,
        ssl.SSLError,
        RuntimeError,
    ) as exc:
        result = {"error": type(exc).__name__, "detail": str(exc)}
    print(json.dumps(result))


if __name__ == "__main__":
    sys.exit(main())

"""Authenticated REST + WebSocket gateway (ADR 0001/0002/0003).

This is a security boundary in front of Kenpi Core, not a second implementation of it:
it reads state through a provider and subscribes to the core event bus. It exposes no
camera command routes. REST gives snapshots; the WebSocket is a push channel only.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import ssl
import time
from collections.abc import Callable
from datetime import datetime, timezone

from kenpi.core.events import Event, EventBus
from kenpi.gateway.websocket import (
    OP_CLOSE,
    OP_PING,
    OP_PONG,
    OP_TEXT,
    WebSocketError,
    accept_key,
    close_frame,
    encode_frame,
    read_frame,
)
from kenpi.security.devices import AuthError, Device, DeviceRegistry, RateLimiter, b64d

logger = logging.getLogger(__name__)
SCHEMA_VERSION = 1
SCOPE_BY_FAMILY = {
    "system": "status.read",
    "vehicle": "vehicle.read",
    "camera": "camera.read",
    "video": "camera.read",
    "media": "media.read",
}
STATUS = {
    "invalid_token": 401,
    "invalid_credential": 401,
    "expired_credential": 401,
    "bad_proof": 401,
    "remote_not_permitted": 403,
    "rate_limited": 429,
}
REASONS = {200: "OK", 400: "Bad Request", 401: "Unauthorized", 403: "Forbidden",
           404: "Not Found", 405: "Method Not Allowed", 413: "Payload Too Large",
           429: "Too Many Requests", 431: "Request Header Fields Too Large",
           503: "Service Unavailable"}  # fmt: skip
LOCAL_NETWORKS = tuple(
    ipaddress.ip_network(n)
    for n in (
        "127.0.0.0/8", "::1/128", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
        "169.254.0.0/16", "fe80::/10", "fc00::/7",
    )
)  # fmt: skip


def envelope(event: Event) -> dict[str, object]:
    family = event.type.value.split(".", 1)[0]
    return {
        "event_id": event.id,
        "sequence": event.seq,
        "timestamp": datetime.fromtimestamp(event.timestamp, timezone.utc).isoformat(),
        "type": event.type.value,
        "schema_version": SCHEMA_VERSION,
        "source": family,
        "payload": dict(event.data),
    }


def event_scope(event: Event) -> str:
    return SCOPE_BY_FAMILY.get(event.type.value.split(".", 1)[0], "status.read")


def validate_bind_host(host: str) -> None:
    """Refuse wildcard and publicly routable bind addresses."""
    address = ipaddress.ip_address(host)
    if address.is_unspecified or not any(address in net for net in LOCAL_NETWORKS):
        raise ValueError(
            f"gateway host {host} must be a private, loopback or link-local address"
        )


class GatewayServer:
    def __init__(
        self,
        registry: DeviceRegistry,
        events: EventBus,
        snapshot_provider: Callable[[frozenset[str]], dict[str, object]],
        hosts: tuple[str, ...],
        port: int,
        ssl_context: ssl.SSLContext | None,
        remote_networks: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (),
        remote_enabled: Callable[[], bool] = lambda: False,
        max_connections: int = 8,
        heartbeat_seconds: float = 15.0,
        idle_timeout_seconds: float = 45.0,
        auth_timeout_seconds: float = 5.0,
        revalidate_seconds: float = 2.0,
        queue_size: int = 64,
        wireguard_info: Callable[[Device], dict | None] = lambda device: None,
        rebind_seconds: float = 5.0,
    ) -> None:
        for host in hosts:
            validate_bind_host(host)
        if ssl_context is None and not all(
            ipaddress.ip_address(h).is_loopback for h in hosts
        ):
            raise ValueError("TLS is required unless the gateway binds loopback only")
        self._registry, self._events, self._snapshot = (
            registry,
            events,
            snapshot_provider,
        )
        self._hosts, self._port, self._ssl = hosts, port, ssl_context
        self._remote_networks, self._remote_enabled = remote_networks, remote_enabled
        self._max = max_connections
        self._heartbeat, self._idle = heartbeat_seconds, idle_timeout_seconds
        self._auth_timeout, self._revalidate = auth_timeout_seconds, revalidate_seconds
        self._queue_size = queue_size
        self._servers: list[asyncio.Server] = []
        self._tasks: set[asyncio.Task[None]] = set()
        self._auth_limiter = RateLimiter(10, 60.0)
        self._pair_limiter = RateLimiter(30, 60.0)
        self.bound_ports: list[int] = []
        self._wireguard_info, self._rebind_seconds = wireguard_info, rebind_seconds
        self._unbound: list[str] = []
        self._rebinder: asyncio.Task[None] | None = None

    async def _bind(self, host: str) -> bool:
        try:
            server = await asyncio.start_server(
                self._handle, host, self._port, ssl=self._ssl, limit=16384
            )
        except OSError as exc:
            if any(ipaddress.ip_address(host) in n for n in self._remote_networks):
                logger.warning("Remote gateway address %s unavailable: %s", host, exc)
                return False
            raise
        self._servers.append(server)
        self.bound_ports.append(server.sockets[0].getsockname()[1])
        return True

    async def start(self) -> None:
        for host in self._hosts:
            try:
                bound = await self._bind(host)
            except OSError:
                await self.stop()
                raise
            if not bound:
                self._unbound.append(host)
        if not self._servers:
            raise OSError("gateway could not bind any address")
        if self._unbound:
            self._rebinder = asyncio.create_task(self._rebind_loop())

    async def _rebind_loop(self) -> None:
        # The WireGuard address only exists while the tunnel is up; bind it when it appears.
        while self._unbound:
            await asyncio.sleep(self._rebind_seconds)
            for host in list(self._unbound):
                if await self._bind(host):
                    self._unbound.remove(host)
                    logger.info("Remote gateway address %s now bound", host)

    async def stop(self) -> None:
        if self._rebinder:
            self._rebinder.cancel()
            await asyncio.gather(self._rebinder, return_exceptions=True)
            self._rebinder = None
        for server in self._servers:
            server.close()
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        for server in self._servers:
            await server.wait_closed()
        self._servers.clear()

    # ---- HTTP -------------------------------------------------------------------

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        task = asyncio.current_task()
        assert task is not None
        if len(self._tasks) >= self._max:
            await self._respond(writer, 503, {"error": "too_many_connections"})
            writer.close()
            return
        self._tasks.add(task)
        try:
            await self._serve(reader, writer)
        except (asyncio.CancelledError, ConnectionError, asyncio.IncompleteReadError):
            pass
        except Exception:
            logger.exception("Gateway connection failed")
        finally:
            self._tasks.discard(task)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    def _zone(self, peer: str) -> str | None:
        address = ipaddress.ip_address(peer)
        if any(address in n for n in self._remote_networks):
            return "remote"
        return "local" if any(address in n for n in LOCAL_NETWORKS) else None

    @staticmethod
    async def _respond(
        writer, status: int, body: dict | None = None, extra: str = ""
    ) -> None:
        data = json.dumps(body if body is not None else {}).encode()
        head = (
            f"HTTP/1.1 {status} {REASONS.get(status, 'Error')}\r\n"
            "Content-Type: application/json\r\nCache-Control: no-store\r\n"
            f"Content-Length: {len(data)}\r\nConnection: close\r\n{extra}\r\n"
        )
        try:
            writer.write(head.encode() + data)
            await writer.drain()
        except ConnectionError:
            pass

    async def _serve(self, reader, writer) -> None:
        peer = writer.get_extra_info("peername")[0].split("%")[0]
        zone = self._zone(peer)
        if zone is None or (zone == "remote" and not self._remote_enabled()):
            self._registry.audit("connection_refused", peer=peer, zone=zone)
            await self._respond(writer, 403, {"error": "forbidden"})
            return
        try:
            raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
        except (asyncio.LimitOverrunError, asyncio.TimeoutError):
            await self._respond(writer, 431, {"error": "bad_request"})
            return
        lines = raw.decode("latin-1").split("\r\n")
        try:
            method, target, _ = lines[0].split(" ", 2)
        except ValueError:
            await self._respond(writer, 400, {"error": "bad_request"})
            return
        headers = {}
        for line in lines[1:]:
            if ":" in line:
                key, _, value = line.partition(":")
                headers[key.strip().lower()] = value.strip()
        path = target.split("?", 1)[0]
        if path == "/ws":
            await self._websocket(reader, writer, headers, zone, peer, method)
            return
        body: dict = {}
        if method == "POST":
            try:
                length = int(headers.get("content-length", "0"))
            except ValueError:
                length = -1
            if not 0 <= length <= 4096:
                await self._respond(writer, 413, {"error": "bad_length"})
                return
            try:
                body = json.loads(
                    await asyncio.wait_for(reader.readexactly(length), 10)
                )
                if not isinstance(body, dict):
                    raise ValueError
            except (ValueError, asyncio.TimeoutError):
                await self._respond(writer, 400, {"error": "bad_json"})
                return
        try:
            status, payload = await self._route(method, path, headers, body, zone, peer)
        except AuthError as exc:
            status, payload = STATUS.get(exc.code, 400), {"error": exc.code}
        await self._respond(writer, status, payload)

    async def _route(self, method, path, headers, body, zone, peer) -> tuple[int, dict]:
        if method == "POST" and path.startswith(("/pair/", "/auth/")):
            limiter = (
                self._pair_limiter if path.startswith("/pair/") else self._auth_limiter
            )
            if not limiter.allow(peer):
                raise AuthError("rate_limited")
            if path.startswith("/pair/") and zone != "local":
                raise AuthError("remote_not_permitted")
        if method == "POST" and path == "/pair/enroll":
            self._registry.submit_enrollment(
                _text(body, "enrollment_id"), _text(body, "challenge"),
                _text(body, "name"), _text(body, "public_key"), _text(body, "signature"),
                body.get("wireguard_key") if isinstance(body.get("wireguard_key"), str) else None,
            )  # fmt: skip
            return 200, {"state": "submitted"}
        if method == "POST" and path == "/pair/status":
            state, tokens = self._registry.collect(
                _text(body, "enrollment_id"), _text(body, "signature")
            )
            result: dict[str, object] = {"state": state}
            if tokens is not None:
                result.update(
                    device_id=tokens.device_id,
                    access_token=tokens.access_token,
                    expires_in=tokens.access_expires_in,
                    refresh_token=tokens.refresh_token,
                )
            return 200, result
        if method == "POST" and path == "/auth/refresh":
            tokens = self._registry.refresh(
                _text(body, "refresh_token"), _text(body, "signature")
            )
            return 200, {
                "device_id": tokens.device_id,
                "access_token": tokens.access_token,
                "expires_in": tokens.access_expires_in,
                "refresh_token": tokens.refresh_token,
            }
        if method == "GET" and path == "/api/v1/snapshot":
            device = self._authenticated(headers, zone)
            return 200, self._snapshot_message(device)
        if method == "GET" and path == "/api/v1/wireguard":
            device = self._authenticated(headers, zone)
            info = self._wireguard_info(device)
            if info is None:
                return 404, {"error": "not_found"}
            return 200, {"wireguard": info}
        return (405 if path in {"/pair/enroll", "/pair/status", "/auth/refresh", "/api/v1/snapshot", "/api/v1/wireguard"} else 404), {"error": "not_found"}  # fmt: skip

    def _authenticated(self, headers: dict, zone: str) -> Device:
        scheme, _, token = headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise AuthError("invalid_token")
        return self._registry.authenticate(
            token, zone == "remote", self._remote_enabled()
        )

    def _snapshot_message(self, device: Device) -> dict:
        latest = self._events.events_after(0, 1)
        return {
            "type": "snapshot",
            "schema_version": SCHEMA_VERSION,
            "stream_id": latest["stream_id"],
            "sequence": latest["latest_seq"],
            "state": self._snapshot(device.scopes),
        }

    # ---- WebSocket --------------------------------------------------------------

    async def _websocket(self, reader, writer, headers, zone, peer, method) -> None:
        key = headers.get("sec-websocket-key", "")
        valid = (
            method == "GET"
            and headers.get("upgrade", "").lower() == "websocket"
            and "upgrade" in headers.get("connection", "").lower()
            and headers.get("sec-websocket-version") == "13"
            and len(key) == 24
        )
        if valid:
            try:
                valid = len(b64d(key)) == 16
            except AuthError:
                valid = False
        if not valid:
            await self._respond(writer, 400, {"error": "bad_upgrade"})
            return
        if not self._auth_limiter.allow(peer):
            await self._respond(writer, 429, {"error": "rate_limited"})
            return
        writer.write(
            (
                "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                f"Connection: Upgrade\r\nSec-WebSocket-Accept: {accept_key(key)}\r\n\r\n"
            ).encode()
        )
        await writer.drain()
        session = _Session(self, reader, writer, zone, peer)
        await session.run()


def _text(body: dict, key: str) -> str:
    value = body.get(key)
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise AuthError("bad_request")
    return value


class _Session:
    def __init__(
        self, gateway: GatewayServer, reader, writer, zone: str, peer: str
    ) -> None:
        self.g, self.reader, self.writer, self.zone, self.peer = (
            gateway,
            reader,
            writer,
            zone,
            peer,
        )
        self.device: Device | None = None
        self.token = ""
        self.queue: asyncio.Queue[Event] = asyncio.Queue(gateway._queue_size)
        self.overflow = False
        self.sent_seq = 0
        self.last_rx = time.monotonic()
        self.closing: tuple[int, str] | None = None
        self.loop = asyncio.get_running_loop()
        self.messages = RateLimiter(30, 1.0)

    def _send(self, message: dict) -> None:
        self.writer.write(encode_frame(OP_TEXT, json.dumps(message).encode()))

    def _authenticate(self, token: str) -> Device:
        return self.g._registry.authenticate(
            token, self.zone == "remote", self.g._remote_enabled()
        )

    def _offer(self, event: Event) -> None:
        if self.queue.full():
            self.overflow = True
            return
        self.queue.put_nowait(event)

    def _on_event(self, event: Event) -> None:
        try:
            self.loop.call_soon_threadsafe(self._offer, event)
        except RuntimeError:
            pass

    async def run(self) -> None:
        unsubscribe = None
        try:
            if not await self._handshake():
                return
            unsubscribe = self.g._events.subscribe(self._on_event)
            await self._initial_sync()
            reader_task = asyncio.create_task(self._read_loop())
            try:
                await self._write_loop(reader_task)
            finally:
                reader_task.cancel()
                await asyncio.gather(reader_task, return_exceptions=True)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            if unsubscribe:
                unsubscribe()
            code, reason = self.closing or (1001, "going away")
            try:
                self.writer.write(close_frame(code, reason))
                await self.writer.drain()
            except Exception:
                pass

    async def _handshake(self) -> bool:
        try:
            opcode, data = await asyncio.wait_for(
                read_frame(self.reader), self.g._auth_timeout
            )
            message = json.loads(data) if opcode == OP_TEXT else None
            if not isinstance(message, dict) or message.get("type") != "auth":
                raise AuthError("auth_required")
            token = message.get("token")
            if not isinstance(token, str):
                raise AuthError("invalid_token")
            self.device = self._authenticate(token)
            self.token = token
            self.hello = message
        except (AuthError, ValueError, asyncio.TimeoutError, WebSocketError) as exc:
            code = getattr(exc, "code", "auth_timeout")
            self.g._registry.audit("ws_auth_failed", peer=self.peer, why=str(code))
            self.closing = (4401, str(code)[:60])
            return False
        return True

    def _snapshot_message(self, kind: str) -> dict:
        assert self.device is not None
        message = self.g._snapshot_message(self.device)
        message["type"] = kind
        self.sent_seq = message["sequence"]
        return message

    async def _initial_sync(self) -> None:
        hello, bus = self.hello, self.g._events
        last, stream = hello.get("last_sequence"), hello.get("stream_id")
        if type(last) is int and stream == bus.stream_id and last >= 0:
            replay = bus.events_after(last, 10_000)
            if not replay["gap"] and last <= replay["latest_seq"]:
                self._send(
                    {"type": "resumed", "schema_version": SCHEMA_VERSION,
                     "stream_id": bus.stream_id, "sequence": replay["latest_seq"]}
                )  # fmt: skip
                self.sent_seq = last
                for item in replay["events"]:
                    self._emit(item)
                self.sent_seq = max(self.sent_seq, replay["latest_seq"])
                await self.writer.drain()
                return
        self._send(self._snapshot_message("snapshot"))
        await self.writer.drain()

    def _emit(self, item: Event | dict) -> None:
        assert self.device is not None
        if isinstance(item, dict):
            family = str(item["type"]).split(".", 1)[0]
            scope = SCOPE_BY_FAMILY.get(family, "status.read")
            if scope in self.device.scopes:
                self._send(
                    {"type": "event", "schema_version": SCHEMA_VERSION,
                     "event": {"event_id": item["id"], "sequence": item["seq"],
                               "timestamp": datetime.fromtimestamp(item["timestamp"], timezone.utc).isoformat(),
                               "type": item["type"], "schema_version": SCHEMA_VERSION,
                               "source": family, "payload": item["data"]}}
                )  # fmt: skip
            return
        if event_scope(item) in self.device.scopes:
            self._send(
                {
                    "type": "event",
                    "schema_version": SCHEMA_VERSION,
                    "event": envelope(item),
                }
            )

    async def _read_loop(self) -> None:
        while True:
            try:
                opcode, data = await read_frame(self.reader)
            except WebSocketError as exc:
                self.closing = (exc.code, exc.reason)
                return
            self.last_rx = time.monotonic()
            if not self.messages.allow("c"):
                self.closing = (1008, "message rate exceeded")
                return
            if opcode == OP_CLOSE:
                self.closing = (1000, "bye")
                return
            if opcode == OP_PING:
                self.writer.write(encode_frame(OP_PONG, data))
            elif opcode == OP_TEXT:
                await self._client_message(data)
                if self.closing:
                    return

    async def _client_message(self, data: bytes) -> None:
        try:
            message = json.loads(data)
        except ValueError:
            self.closing = (1007, "invalid json")
            return
        kind = message.get("type") if isinstance(message, dict) else None
        if kind == "reauth" and isinstance(message.get("token"), str):
            try:
                device = self._authenticate(message["token"])
                if device.device_id != (self.device.device_id if self.device else None):
                    raise AuthError("device_mismatch")
            except AuthError as exc:
                self.closing = (4401, exc.code)
                return
            self.token, self.device = message["token"], device
        elif kind == "ping":
            self._send({"type": "pong"})
        # Unknown message types are ignored; this channel carries no commands.

    async def _write_loop(self, reader_task: asyncio.Task) -> None:
        next_beat = time.monotonic() + self.g._heartbeat
        while not reader_task.done() and not self.closing:
            timeout = min(self.g._revalidate, max(0.0, next_beat - time.monotonic()))
            try:
                event = await asyncio.wait_for(self.queue.get(), timeout or 0.01)
                if self.overflow:
                    self._resync()
                elif event.seq > self.sent_seq:
                    self._emit(event)
                    self.sent_seq = event.seq
            except asyncio.TimeoutError:
                pass
            if self.overflow:
                self._resync()
            now = time.monotonic()
            if now - self.last_rx > self.g._idle:
                self.closing = (1001, "idle timeout")
            elif not self.g._registry.token_valid(self.token):
                self.closing = (4401, "token expired or revoked")
            elif self.zone == "remote" and not self.g._remote_enabled():
                self.closing = (4403, "remote access disabled")
            elif now >= next_beat:
                latest = self.g._events.events_after(0, 1)["latest_seq"]
                self._send({"type": "heartbeat", "sequence": latest})
                self.writer.write(encode_frame(OP_PING))
                next_beat = now + self.g._heartbeat
            await self.writer.drain()

    def _resync(self) -> None:
        while not self.queue.empty():
            self.queue.get_nowait()
        self.overflow = False
        self._send(self._snapshot_message("resync"))

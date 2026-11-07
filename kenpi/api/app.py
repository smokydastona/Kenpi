"""Loopback-only, read-only HTTP API: health, capabilities, status, events, media."""

from __future__ import annotations

import asyncio
import importlib.metadata
import ipaddress
import json
import logging
import socket
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from typing import Callable
from urllib.parse import parse_qs

from kenpi.core.capabilities import Capability, INITIAL_CAPABILITIES
from kenpi.core.events import EventBus
from kenpi.headunit.profiles import list_profiles
from kenpi.media.library import LocalMediaLibrary, MediaError
from kenpi.services.health import collect_host_health

logger = logging.getLogger(__name__)

try:
    _KENPI_VERSION = importlib.metadata.version("kenpi")
except importlib.metadata.PackageNotFoundError:
    _KENPI_VERSION = "unknown"


def capability_payload(capabilities: tuple[Capability, ...]) -> dict[str, object]:
    return {
        "capabilities": [
            {
                "name": item.name,
                "state": item.state.value,
                "evidence": item.evidence.name,
                "detail": item.detail,
            }
            for item in capabilities
        ]
    }


class _TokenBucket:
    def __init__(self, rate_per_second: float, burst: int) -> None:
        self._rate = rate_per_second
        self._burst = float(burst)
        self._tokens = float(burst)
        self._updated = time.monotonic()
        self._lock = threading.Lock()

    def allow(self) -> bool:
        with self._lock:
            now = time.monotonic()
            self._tokens = min(
                self._burst, self._tokens + (now - self._updated) * self._rate
            )
            self._updated = now
            if self._tokens < 1.0:
                return False
            self._tokens -= 1.0
            return True


class KenpiApiService:
    """A read-only local API. Every route is GET; there are no command or write routes."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8080,
        capabilities: tuple[Capability, ...] = INITIAL_CAPABILITIES,
        health_check: Callable[[], dict[str, object]] | None = None,
        status_provider: Callable[[], dict[str, object]] | None = None,
        head_unit: str = "kenwood-dmx5710s",
        vehicle_provider: Callable[[], dict[str, object]] | None = None,
        media_library: LocalMediaLibrary | None = None,
        safe_mode: bool = False,
        events: EventBus | None = None,
    ) -> None:
        try:
            address = ipaddress.ip_address(host)
        except ValueError as exc:
            raise ValueError("API host must be a loopback IP address") from exc
        if not address.is_loopback:
            raise ValueError("unauthenticated API must bind to loopback")
        if type(port) is not int or not 0 <= port <= 65535:
            raise ValueError("API port must be between 0 and 65535")
        self._host = host
        self._port = port
        self._capabilities = capabilities
        self._health_check = health_check or collect_host_health
        self._status_provider = status_provider
        self._head_unit = head_unit
        self._vehicle_provider = vehicle_provider
        self._media_library = media_library
        self._safe_mode = safe_mode
        self._events = events
        self._server: ThreadingHTTPServer | None = None
        self._server_task: asyncio.Task[None] | None = None

    @property
    def bound_port(self) -> int | None:
        if self._server is None:
            return None
        return int(self._server.server_address[1])

    async def start(self) -> None:
        if self._server is not None:
            raise RuntimeError("API is already running")
        capabilities = self._capabilities
        health_check = self._health_check
        status_provider = self._status_provider
        selected_head_unit = self._head_unit
        vehicle_provider = self._vehicle_provider
        media_library = self._media_library
        safe_mode = self._safe_mode
        events = self._events
        limiter = _TokenBucket(rate_per_second=50.0, burst=100)
        started_at_monotonic = time.monotonic()
        started_at_iso = datetime.now(timezone.utc).isoformat()

        def build_status_payload() -> dict[str, object]:
            payload: dict[str, object] = {
                "version": _KENPI_VERSION,
                "started_at": started_at_iso,
                "uptime_seconds": time.monotonic() - started_at_monotonic,
            }
            if status_provider is not None:
                try:
                    payload["coordinator"] = status_provider()
                except Exception:
                    logger.warning("Coordinator status collection failed")
                    payload["coordinator"] = None
                    payload["coordinator_error"] = "status collection failed"
            return payload

        assets = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/static/dashboard.css": ("dashboard.css", "text/css; charset=utf-8"),
            "/static/dashboard.js": ("dashboard.js", "text/javascript; charset=utf-8"),
        }

        def disabled(name: str) -> dict[str, object]:
            match = next((c for c in capabilities if c.name == name), None)
            return {
                "available": False,
                "state": match.state.value if match else "unsupported",
                "evidence": match.evidence.name if match else "E0",
                "reason": match.detail if match else "capability is not declared",
            }

        class Handler(BaseHTTPRequestHandler):
            timeout = 10.0

            def handle(self) -> None:
                try:
                    super().handle()
                except (ConnectionError, TimeoutError):
                    logger.debug("Local status client disconnected or timed out")
                except OSError as exc:
                    logger.warning(
                        "Local status request failed: %s", type(exc).__name__
                    )

            def do_GET(self) -> None:
                if not limiter.allow():
                    self._send_json(429, {"error": "rate_limited"})
                    return
                path = self.path.split("?", 1)[0]
                if path.startswith("/api/v1/media/") and len(path) > 14:
                    self._send_media(path[len("/api/v1/media/") :])
                    return
                asset = assets.get(self.path)
                if asset is not None:
                    name, content_type = asset
                    body = files("kenpi.ui").joinpath(name).read_bytes()
                    self._send(200, body, content_type)
                elif self.path == "/healthz":
                    try:
                        body = self._encode_json(health_check())
                    except Exception:
                        logger.warning("Host health collection failed")
                        self._send_json(503, {"application_status": "degraded"})
                    else:
                        self._send(200, body, "application/json; charset=utf-8")
                elif self.path == "/api/v1/capabilities":
                    self._send_json(200, capability_payload(capabilities))
                elif self.path == "/api/v1/head-units":
                    self._send_json(
                        200,
                        {"selected": selected_head_unit, "head_units": list_profiles()},
                    )
                elif self.path == "/api/v1/camera":
                    self._send_json(200, disabled("camera_control"))
                elif self.path == "/api/v1/preview":
                    self._send_json(200, disabled("live_preview"))
                elif self.path == "/api/v1/vehicle":
                    if vehicle_provider is None:
                        self._send_json(
                            200,
                            {
                                "enabled": False,
                                "reason": "vehicle_uart_enabled is false",
                                **disabled("vehicle_power_coordination"),
                            },
                        )
                    else:
                        self._send_json(
                            200, {"enabled": True, "link": vehicle_provider()}
                        )
                elif self.path == "/api/v1/media":
                    camera = disabled("media_access")
                    if media_library is None:
                        self._send_json(200, {"local_enabled": False, "camera": camera})
                    else:
                        try:
                            body = {
                                "local_enabled": True,
                                "files": [
                                    item.describe()
                                    for item in media_library.list_media()
                                ],
                                "storage": media_library.storage(),
                                "camera": camera,
                            }
                        except OSError:
                            logger.warning("Media library listing failed")
                            self._send_json(503, {"error": "media_unavailable"})
                        else:
                            self._send_json(200, body)
                elif self.path == "/api/v1/diagnostics":
                    self._send_json(
                        200,
                        {
                            "version": _KENPI_VERSION,
                            "head_unit": selected_head_unit,
                            "safe_mode": safe_mode,
                            "capability_states": {
                                c.name: c.state.value for c in capabilities
                            },
                            "status": build_status_payload(),
                        },
                    )
                elif path == "/api/v1/events":
                    if events is None:
                        self._send_json(503, {"error": "events_unavailable"})
                    else:
                        query = parse_qs(self.path.partition("?")[2])
                        try:
                            after = int(query.get("after", ["0"])[0])
                            limit = int(query.get("limit", ["100"])[0])
                            if after < 0 or not 1 <= limit <= 500:
                                raise ValueError
                        except ValueError:
                            self._send_json(400, {"error": "bad_query"})
                        else:
                            self._send_json(200, events.events_after(after, limit))
                elif self.path == "/api/v1/status":
                    self._send_json(200, build_status_payload())
                else:
                    self._send_json(404, {"error": "not_found"})

            def _send_media(self, name: str) -> None:
                if media_library is None:
                    self._send_json(404, {"error": "not_found"})
                    return
                try:
                    path = media_library.open_path(name)
                    size = path.stat().st_size
                    handle = path.open("rb")
                except (MediaError, OSError):
                    self._send_json(404, {"error": "not_found"})
                    return
                with handle:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Length", str(size))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.end_headers()
                    while chunk := handle.read(64 * 1024):
                        self.wfile.write(chunk)

            def _send_json(self, status: int, value: dict[str, object]) -> None:
                self._send(
                    status, self._encode_json(value), "application/json; charset=utf-8"
                )

            @staticmethod
            def _encode_json(value: dict[str, object]) -> bytes:
                return json.dumps(
                    value, separators=(",", ":"), ensure_ascii=True
                ).encode("utf-8")

            def _send(self, status: int, body: bytes, content_type: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                if content_type.startswith("text/html"):
                    self.send_header(
                        "Content-Security-Policy",
                        "default-src 'self'; connect-src 'self'; script-src 'self'; style-src 'self'",
                    )
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                return

        class LocalServer(ThreadingHTTPServer):
            address_family = socket.AF_INET6 if ":" in self._host else socket.AF_INET
            daemon_threads = True
            allow_reuse_address = True

            def __init__(self, *args: object, **kwargs: object) -> None:
                self._slots = threading.BoundedSemaphore(16)
                self._requests: set[socket.socket] = set()
                self._requests_lock = threading.Lock()
                super().__init__(*args, **kwargs)

            def shutdown_request(self, request: socket.socket) -> None:
                with self._requests_lock:
                    self._requests.discard(request)
                super().shutdown_request(request)

            def server_close(self) -> None:
                super().server_close()
                with self._requests_lock:
                    requests = tuple(self._requests)
                for request in requests:
                    self.shutdown_request(request)

            def process_request(
                self, request: socket.socket, client_address: object
            ) -> None:
                if not self._slots.acquire(blocking=False):
                    self.shutdown_request(request)
                    return
                try:
                    super().process_request(request, client_address)
                except BaseException:
                    self._slots.release()
                    raise

            def process_request_thread(
                self, request: socket.socket, client_address: object
            ) -> None:
                try:
                    super().process_request_thread(request, client_address)
                finally:
                    self._slots.release()

            def get_request(self) -> tuple[object, object]:
                request, client_address = super().get_request()
                request.settimeout(5.0)
                with self._requests_lock:
                    self._requests.add(request)
                return request, client_address

        self._server = LocalServer((self._host, self._port), Handler)
        self._server_task = asyncio.create_task(
            asyncio.to_thread(self._server.serve_forever)
        )

    async def stop(self) -> None:
        if self._server is None:
            return
        server = self._server
        task = self._server_task
        self._server = None
        self._server_task = None
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        if task is not None:
            await task

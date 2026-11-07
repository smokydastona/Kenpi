"""Loopback-only read-only HTTP API for health and verified capabilities."""

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

from kenpi.core.capabilities import Capability, INITIAL_CAPABILITIES
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


class KenpiApiService:
    """A read-only local API; no command or media routes are exposed."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8080,
        capabilities: tuple[Capability, ...] = INITIAL_CAPABILITIES,
        health_check: Callable[[], dict[str, object]] | None = None,
        status_provider: Callable[[], dict[str, object]] | None = None,
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

        class Handler(BaseHTTPRequestHandler):
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
                elif self.path == "/api/v1/status":
                    self._send_json(200, build_status_payload())
                else:
                    self._send_json(404, {"error": "not_found"})

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

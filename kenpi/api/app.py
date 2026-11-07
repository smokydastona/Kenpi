"""Loopback-only read-only HTTP API for health and verified capabilities."""

from __future__ import annotations

import asyncio
import ipaddress
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from typing import Callable

from kenpi.core.capabilities import Capability, INITIAL_CAPABILITIES
from kenpi.services.health import collect_host_health


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
    ) -> None:
        try:
            address = ipaddress.ip_address(host)
        except ValueError as exc:
            raise ValueError("API host must be a loopback IP address") from exc
        if not address.is_loopback:
            raise ValueError("unauthenticated API must bind to loopback")
        if not 0 <= port <= 65535:
            raise ValueError("API port must be between 0 and 65535")
        self._host = host
        self._port = port
        self._capabilities = capabilities
        self._health_check = health_check or collect_host_health
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
        assets = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/static/dashboard.css": ("dashboard.css", "text/css; charset=utf-8"),
            "/static/dashboard.js": ("dashboard.js", "text/javascript; charset=utf-8"),
        }

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                asset = assets.get(self.path)
                if asset is not None:
                    name, content_type = asset
                    body = files("kenpi.ui").joinpath(name).read_bytes()
                    self._send(200, body, content_type)
                elif self.path == "/healthz":
                    try:
                        self._send_json(200, health_check())
                    except Exception:
                        self._send_json(503, {"application_status": "degraded"})
                elif self.path == "/api/v1/capabilities":
                    self._send_json(200, capability_payload(capabilities))
                else:
                    self._send_json(404, {"error": "not_found"})

            def _send_json(self, status: int, value: dict[str, object]) -> None:
                body = json.dumps(
                    value, separators=(",", ":"), ensure_ascii=True
                ).encode("utf-8")
                self._send(status, body, "application/json; charset=utf-8")

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
            daemon_threads = True
            allow_reuse_address = True

            def get_request(self) -> tuple[object, object]:
                request, client_address = super().get_request()
                request.settimeout(5.0)
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

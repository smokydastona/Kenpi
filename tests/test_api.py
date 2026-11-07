import asyncio
import json
import socket
import unittest
from urllib.error import HTTPError
from urllib.request import ProxyHandler, build_opener, urlopen

from kenpi.api.app import KenpiApiService


def _ipv6_loopback_usable() -> bool:
    if not socket.has_ipv6:
        return False
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as probe:
            probe.bind(("::1", 0))
    except OSError:
        return False
    return True


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_shutdown_closes_idle_clients(self) -> None:
        service = KenpiApiService(port=0)
        await service.start()
        connection = await asyncio.to_thread(
            socket.create_connection, ("127.0.0.1", service.bound_port), 2
        )
        try:
            connection.sendall(b"G")
            await asyncio.sleep(0.05)
            await service.stop()
            self.assertEqual(await asyncio.to_thread(connection.recv, 1), b"")
            self.assertIsNone(service.bound_port)
        finally:
            connection.close()
            await service.stop()

    async def test_rejects_non_integer_port(self) -> None:
        for port in (True, 8080.0, "8080"):
            with self.subTest(port=port):
                with self.assertRaises(ValueError):
                    KenpiApiService(port=port)

    async def test_idle_connections_are_bounded_and_slots_recover(self) -> None:
        service = KenpiApiService(port=0)
        await service.start()
        connections: list[socket.socket] = []
        try:
            for _ in range(16):
                connection = await asyncio.to_thread(
                    socket.create_connection, ("127.0.0.1", service.bound_port), 2
                )
                connections.append(connection)
                connection.sendall(b"GET /healthz HTTP/1.1\r\n")
            await asyncio.sleep(0.1)
            excess = await asyncio.to_thread(
                socket.create_connection, ("127.0.0.1", service.bound_port), 2
            )
            connections.append(excess)
            self.assertEqual(await asyncio.to_thread(excess.recv, 1), b"")
            for connection in connections:
                connection.close()
            connections.clear()
            await asyncio.sleep(0.1)
            response = await asyncio.to_thread(
                urlopen, f"http://127.0.0.1:{service.bound_port}/healthz"
            )
            with response:
                self.assertEqual(response.status, 200)
        finally:
            for connection in connections:
                connection.close()
            await service.stop()

    @unittest.skipUnless(_ipv6_loopback_usable(), "IPv6 loopback is unavailable")
    async def test_ipv6_loopback_status_and_restart(self) -> None:
        service = KenpiApiService(
            host="::1", port=0, health_check=lambda: {"application_status": "ok"}
        )
        opener = build_opener(ProxyHandler({}))
        for _ in range(2):
            await service.start()
            try:
                response = await asyncio.to_thread(
                    opener.open, f"http://[::1]:{service.bound_port}/healthz"
                )
                with response:
                    self.assertEqual(
                        json.loads(response.read()), {"application_status": "ok"}
                    )
            finally:
                await service.stop()
            self.assertIsNone(service.bound_port)

    async def test_health_failure_is_degraded_not_success(self) -> None:
        def broken_health() -> dict[str, object]:
            raise OSError("unavailable")

        service = KenpiApiService(port=0, health_check=broken_health)
        await service.start()
        try:
            with self.assertRaises(HTTPError) as caught:
                await asyncio.to_thread(
                    urlopen, f"http://127.0.0.1:{service.bound_port}/healthz"
                )
            self.assertEqual(caught.exception.code, 503)
            self.assertEqual(
                json.loads(caught.exception.read()),
                {"application_status": "degraded"},
            )
        finally:
            await service.stop()

    async def test_api_refuses_non_loopback_bind(self) -> None:
        with self.assertRaises(ValueError):
            KenpiApiService(host="0.0.0.0")

    async def test_status_routes_serve_live_capability_and_health_data(self) -> None:
        service = KenpiApiService(
            port=0, health_check=lambda: {"application_status": "ok"}
        )
        await service.start()
        try:
            base_url = f"http://127.0.0.1:{service.bound_port}"
            response = await asyncio.to_thread(
                urlopen, f"{base_url}/api/v1/capabilities"
            )
            payload = json.loads(response.read())
            self.assertEqual(payload["capabilities"][0]["state"], "blocked")

            health = await asyncio.to_thread(urlopen, f"{base_url}/healthz")
            self.assertEqual(json.loads(health.read()), {"application_status": "ok"})
            self.assertEqual(health.headers["Cache-Control"], "no-store")

            dashboard = await asyncio.to_thread(urlopen, base_url)
            self.assertIn(
                "script-src 'self'", dashboard.headers["Content-Security-Policy"]
            )
            self.assertIn(b"Loading current service capabilities", dashboard.read())
            stylesheet = await asyncio.to_thread(
                urlopen, f"{base_url}/static/dashboard.css"
            )
            self.assertIn(b"color-scheme", stylesheet.read())
            script = await asyncio.to_thread(urlopen, f"{base_url}/static/dashboard.js")
            self.assertIn(b"/api/v1/capabilities", script.read())

            with self.assertRaises(HTTPError) as error:
                await asyncio.to_thread(urlopen, f"{base_url}/missing")
            self.assertEqual(error.exception.code, 404)
        finally:
            await service.stop()

    async def test_default_health_reports_unavailable_metrics_as_null_or_real_values(
        self,
    ) -> None:
        service = KenpiApiService(port=0)
        await service.start()
        try:
            response = await asyncio.to_thread(
                urlopen, f"http://127.0.0.1:{service.bound_port}/healthz"
            )
            health = json.loads(response.read())
            self.assertEqual(health["application_status"], "ok")
            self.assertIn("cpu_temperature_c", health)
            self.assertIn("memory_available_bytes", health)
            self.assertIn("storage_free_bytes", health)
        finally:
            await service.stop()


if __name__ == "__main__":
    unittest.main()

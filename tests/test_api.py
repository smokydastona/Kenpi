import asyncio
import json
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen

from kenpi.api.app import KenpiApiService


class ApiTests(unittest.IsolatedAsyncioTestCase):
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

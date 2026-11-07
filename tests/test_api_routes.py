import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

from kenpi.api.app import KenpiApiService
from kenpi.media.library import LocalMediaLibrary


async def fetch(url: str) -> tuple[int, bytes]:
    def go() -> tuple[int, bytes]:
        try:
            with urlopen(url) as response:
                return response.status, response.read()
        except HTTPError as exc:
            return exc.code, exc.read()

    return await asyncio.to_thread(go)


class RouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "clip.mp4").write_bytes(b"abc" * 1000)
        self.service = KenpiApiService(
            port=0,
            media_library=LocalMediaLibrary(self.root, 0),
            vehicle_provider=lambda: {"link_state": "no_data"},
        )
        await self.service.start()
        self.addAsyncCleanup(self.service.stop)
        self.base = f"http://127.0.0.1:{self.service.bound_port}"

    async def test_disabled_capabilities_are_structured_not_fabricated(self) -> None:
        for route, name in (("camera", "camera_control"), ("preview", "live_preview")):
            status, body = await fetch(f"{self.base}/api/v1/{route}")
            payload = json.loads(body)
            self.assertEqual(status, 200)
            self.assertFalse(payload["available"], name)
            self.assertEqual(payload["state"], "blocked")

    async def test_vehicle_route_reports_link(self) -> None:
        _, body = await fetch(f"{self.base}/api/v1/vehicle")
        self.assertEqual(
            json.loads(body), {"enabled": True, "link": {"link_state": "no_data"}}
        )

    async def test_vehicle_disabled_without_provider(self) -> None:
        service = KenpiApiService(port=0)
        await service.start()
        try:
            _, body = await fetch(
                f"http://127.0.0.1:{service.bound_port}/api/v1/vehicle"
            )
        finally:
            await service.stop()
        payload = json.loads(body)
        self.assertFalse(payload["enabled"])
        self.assertFalse(payload["available"])

    async def test_media_listing_and_download(self) -> None:
        _, body = await fetch(f"{self.base}/api/v1/media")
        payload = json.loads(body)
        self.assertEqual(payload["files"][0]["name"], "clip.mp4")
        self.assertFalse(payload["camera"]["available"])
        status, data = await fetch(f"{self.base}/api/v1/media/clip.mp4")
        self.assertEqual((status, data), (200, b"abc" * 1000))

    async def test_media_rejects_traversal_and_unknown(self) -> None:
        for path in ("missing.mp4", "..%2Fetc%2Fpasswd", "%2e%2e/x", ".hidden"):
            status, _ = await fetch(f"{self.base}/api/v1/media/{path}")
            self.assertEqual(status, 404, path)

    async def test_diagnostics_route(self) -> None:
        _, body = await fetch(f"{self.base}/api/v1/diagnostics")
        payload = json.loads(body)
        self.assertEqual(payload["capability_states"]["camera_control"], "blocked")
        self.assertIn("status", payload)
        self.assertFalse(payload["safe_mode"])

    async def test_rate_limit_returns_429(self) -> None:
        codes = set()
        for _ in range(3):
            results = await asyncio.gather(
                *(fetch(f"{self.base}/api/v1/camera") for _ in range(40))
            )
            codes.update(code for code, _ in results)
        self.assertIn(429, codes)
        self.assertIn(200, codes)

    async def test_media_route_without_library(self) -> None:
        service = KenpiApiService(port=0)
        await service.start()
        try:
            base = f"http://127.0.0.1:{service.bound_port}"
            _, body = await fetch(f"{base}/api/v1/media")
            self.assertFalse(json.loads(body)["local_enabled"])
            status, _ = await fetch(f"{base}/api/v1/media/clip.mp4")
            self.assertEqual(status, 404)
        finally:
            await service.stop()

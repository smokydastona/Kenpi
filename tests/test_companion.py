import io
import tempfile
import threading
import unittest
import wave
from pathlib import Path

from kenpi.companion import CompanionRegistry, MAX_LINES, MAX_LINE_CHARS, redact
from kenpi.voice.chatterbox_backend import validate_reference
from kenpi.voice.engines import ChatterboxEngine
from kenpi.voice.pipeline_server import serve
from tests.test_assistant import make_assistant
from tests.test_characters import FakeBackend
from tests.test_gateway import GatewayCase


def wav_bytes(seconds: float) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(b"\x00\x00" * int(8000 * seconds))
    return buffer.getvalue()


class ChatterboxTests(unittest.TestCase):
    def test_reference_validation(self) -> None:
        good = wav_bytes(4)
        self.assertEqual(validate_reference(good), good)
        for bad in (wav_bytes(1), b"not a wav"):
            with self.assertRaises(ValueError):
                validate_reference(bad)

    def test_engine_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            server = serve(FakeBackend(), Path(d), "127.0.0.1", 0)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                engine = ChatterboxEngine(
                    f"http://127.0.0.1:{server.server_address[1]}"
                )
                self.assertEqual(engine.id, "chatterbox")
                self.assertTrue(engine.supports_cloning)
                self.assertTrue(engine.availability()[0])
            finally:
                server.shutdown()
                server.server_close()

    def test_enroll_stores_reference_wav(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            assistant = make_assistant(d)

            class Extractor:
                id = "chatterbox"

                def extract_embedding(self, wav):
                    return validate_reference(wav)

            assistant.voice.engines._engines["chatterbox"] = Extractor()
            assistant.enroll_character_voice(
                "southern_fixer",
                wav_bytes(4),
                "Bob",
                "Recorded with permission",
                engine_id="chatterbox",
            )
            profile = assistant.voice.profiles.get("char_southern_fixer")
            self.assertEqual(profile.engine, "chatterbox")
            self.assertEqual(profile.fallback_engine, "melotts")
            self.assertTrue(profile.speaker_embedding.endswith("reference.wav"))
            assistant.remove_character_voice("southern_fixer")
            self.assertFalse(list(Path(d).rglob("reference.wav")))


class RegistryTests(unittest.TestCase):
    def test_redaction(self) -> None:
        text = redact(
            "Authorization: Bearer abc123 token=xyz kenpi://pair?x=1 " + "A" * 40
        )
        for secret in ("abc123", "xyz", "kenpi://", "A" * 40):
            self.assertNotIn(secret, text)

    def test_bounds_and_validation(self) -> None:
        reg = CompanionRegistry()
        kept = reg.report("d", "Pixel", {"lines": ["x" * 999] * 500})
        self.assertEqual(kept, MAX_LINES)
        self.assertLessEqual(len(reg._reports["d"]["lines"][0]), MAX_LINE_CHARS)
        for bad in ([], {"lines": "x"}, {"lines": [1]}, {"connection": 5}):
            with self.assertRaises(ValueError):
                reg.report("d", "Pixel", bad)

    def test_unknown_without_presence_source(self) -> None:
        self.assertFalse(CompanionRegistry().evidence()["presence_known"])


def check_status(assistant) -> tuple[str, str]:
    result = [
        c
        for c in assistant.diagnostics.run().results
        if c.subsystem.value == "companion"
    ][0]
    return result.status.value, result.evidence


class DiagnosticsTests(unittest.TestCase):
    def test_states(self) -> None:
        assistant = make_assistant()
        self.assertEqual(check_status(assistant)[0], "unknown")
        live: list = []
        assistant.companion.set_presence(lambda: live)
        self.assertEqual(check_status(assistant)[0], "unknown")
        assistant.companion.report(
            "p1",
            "Pixel",
            {"connection": "Disconnected: timed out", "lines": ["connect timed out"]},
        )
        self.assertEqual(check_status(assistant)[0], "warn")
        live.append({"device_id": "p1", "name": "Pixel", "since": 0})
        self.assertEqual(check_status(assistant)[0], "ok")

    def test_setup_help_never_claims_unverifiable(self) -> None:
        assistant = make_assistant()
        steps = {i: d for i, d, _ in assistant.setup_steps()}
        self.assertIsNone(steps["pair_phone"])
        self.assertFalse(steps["speech_service"])


class PhoneGatewayTests(GatewayCase):
    async def asyncSetUp(self):
        self.assistant = make_assistant()
        self.kwargs = {
            "assistant_handler": lambda device,
            method,
            path,
            body: self.assistant.phone_request(
                device.device_id, device.name, method, path, body
            )
        }
        await super().asyncSetUp()
        self.assistant.companion.set_presence(self.gateway.connected_devices)

    async def auth(self, scopes):
        creds = await self.enroll(scopes=scopes)
        return {"Authorization": "Bearer " + creds["access_token"]}, creds

    async def test_scope_required(self) -> None:
        headers, _ = await self.auth(["status.read"])
        status, _ = await self.request(
            "GET", "/api/v1/assistant/status", headers=headers
        )
        self.assertEqual(status, 403)
        status, _ = await self.request("GET", "/api/v1/assistant/status")
        self.assertEqual(status, 401)

    async def test_logs_diagnostics_and_ask(self) -> None:
        headers, _ = await self.auth(["status.read", "assistant.use"])
        status, body = await self.request(
            "POST",
            "/api/v1/assistant/logs",
            {"app_version": "1", "connection": "Live", "lines": ["token=SECRET1 ok"]},
            headers,
        )
        self.assertEqual((status, body["accepted"]), (200, 1))
        self.assertNotIn("SECRET1", str(self.assistant.companion.evidence()))
        status, body = await self.request(
            "GET", "/api/v1/assistant/diagnostics", headers=headers
        )
        self.assertEqual(status, 200)
        status, body = await self.request(
            "POST", "/api/v1/assistant/ask", {"text": "help me set up"}, headers
        )
        self.assertEqual(status, 200)
        status, _ = await self.request(
            "POST", "/api/v1/assistant/ask", {"text": ""}, headers
        )
        self.assertEqual(status, 400)

    async def test_presence_follows_websocket(self) -> None:
        headers, creds = await self.auth(["status.read", "assistant.use"])
        self.assertEqual(self.gateway.connected_devices(), [])
        client = await self.ws(creds["access_token"])
        await client.recv_json()
        self.assertEqual(len(self.gateway.connected_devices()), 1)

    async def test_disabled_assistant_returns_404(self) -> None:
        self.gateway._assistant_handler = None
        headers, _ = await self.auth(["assistant.use"])
        status, _ = await self.request(
            "GET", "/api/v1/assistant/status", headers=headers
        )
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()

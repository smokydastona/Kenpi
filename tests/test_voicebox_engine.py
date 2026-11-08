import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from kenpi.assistant import AssistantService, AssistantSettings
from tests.test_assistant import HEALTHY, INITIAL_CAPABILITIES
from kenpi.voice.engines import EngineUnavailable, SynthesisRequest, VoiceboxEngine

WAV = b"RIFF....WAVEfake"


class FakeVoicebox(BaseHTTPRequestHandler):
    statuses = ["generating", "completed"]
    posted: list = []

    def log_message(self, *args):
        pass

    def _send(self, body: bytes, kind="application/json", code=200):
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._send(b'{"status":"healthy","model_loaded":true}')
        elif self.path == "/profiles":
            self._send(json.dumps([{"id": "p1", "name": "Morgan"}]).encode())
        elif self.path.startswith("/generate/g1/status"):
            events = "".join(
                f"data: {json.dumps({'id': 'g1', 'status': s})}\n\n"
                for s in self.statuses
            )
            self._send(events.encode(), "text/event-stream")
        elif self.path == "/audio/g1":
            self._send(WAV, "audio/wav")
        else:
            self._send(b"{}", code=404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        FakeVoicebox.posted.append((self.path, body))
        if self.path == "/generate":
            self._send(b'{"id":"g1","status":"generating"}')
        else:
            self._send(b"{}")


class VoiceboxTests(unittest.TestCase):
    def setUp(self):
        FakeVoicebox.statuses = ["generating", "completed"]
        FakeVoicebox.posted = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeVoicebox)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def engine(self, **kw):
        return VoiceboxEngine(self.url, timeout=5.0, **kw)

    def test_synthesize_flow_sends_only_text_profile_language(self):
        audio = self.engine().synthesize(
            SynthesisRequest("hello", "en", base_speaker="morgan")
        )
        self.assertEqual(audio, WAV)
        path, body = FakeVoicebox.posted[0]
        self.assertEqual(path, "/generate")
        self.assertEqual(
            json.loads(body),
            {"profile_id": "p1", "text": "hello", "language": "en"},
        )

    def test_default_profile_and_missing_profile(self):
        self.assertEqual(
            self.engine(default_profile="Morgan").synthesize(SynthesisRequest("x")),
            WAV,
        )
        with self.assertRaises(EngineUnavailable):
            self.engine().synthesize(SynthesisRequest("x"))
        with self.assertRaises(EngineUnavailable):
            self.engine().synthesize(SynthesisRequest("x", base_speaker="nobody"))

    def test_failed_generation_raises_and_cancels(self):
        FakeVoicebox.statuses = ["failed"]
        with self.assertRaises(EngineUnavailable):
            self.engine().synthesize(SynthesisRequest("x", base_speaker="Morgan"))
        self.assertIn("/generate/g1/cancel", [p for p, _ in FakeVoicebox.posted])

    def test_availability(self):
        self.assertEqual(self.engine().availability(), (True, "ready"))
        self.assertFalse(VoiceboxEngine("").availability()[0])
        self.assertFalse(VoiceboxEngine("http://127.0.0.1:1").availability()[0])

    def test_remote_requires_opt_in(self):
        with self.assertRaises(ValueError):
            VoiceboxEngine("http://192.0.2.10:17493")
        VoiceboxEngine("http://192.0.2.10:17493", allow_remote=True)


class VoiceboxMappingTests(unittest.TestCase):
    def build(self, attestation):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        settings = AssistantSettings(
            voicebox_url="http://127.0.0.1:17493",
            voicebox_profiles={"southern_fixer": "Morgan", "nobody": "X"},
            voicebox_attestation=attestation,
        )
        return AssistantService(
            Path(tmp.name),
            None,
            lambda: HEALTHY,
            INITIAL_CAPABILITIES,
            settings=settings,
        ), tmp

    def test_mapping_needs_attestation(self):
        service, _ = self.build("")
        self.assertEqual(
            service.voice.profiles.get("char_southern_fixer").engine, "openvoice_v2"
        )

    def test_mapping_applies_and_is_not_persisted(self):
        service, tmp = self.build("I own these voices")
        profile = service.voice.profiles.get("char_southern_fixer")
        self.assertEqual((profile.engine, profile.base_speaker), ("voicebox", "Morgan"))
        self.assertEqual(profile.fallback_engine, "melotts")
        service.voice.profiles.activate("char_southern_fixer")
        stored = (Path(tmp.name) / "voice" / "profiles.json").read_text()
        self.assertNotIn("Morgan", stored)


if __name__ == "__main__":
    unittest.main()

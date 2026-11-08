import asyncio
import json
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, build_opener

from kenpi.api.app import KenpiApiService
from kenpi.assistant import AssistantService
from kenpi.core.capabilities import INITIAL_CAPABILITIES
from kenpi.core.permissions import Permission, validate_permission
from kenpi.diagnostics.logs import analyze
from kenpi.diagnostics.models import Status
from kenpi.diagnostics.remediation import RemediationAction, RemediationRegistry
from kenpi.features import FeatureStore, main as features_main
from kenpi.intelligence.planner import Planner
from kenpi.intelligence.tools import Tool, ToolRegistry
from kenpi.voice.catalog import ModelCatalog, ModelEntry, sha256_of
from kenpi.voice.engines import EngineRegistry, EngineUnavailable, OpenVoiceEngine
from kenpi.voice.profiles import ProfileStore, VoiceProfile, new_consent
from kenpi.voice.service import VoiceService

HEALTHY = {
    "memory_total_bytes": 1000,
    "memory_available_bytes": 500,
    "storage_total_bytes": 1000,
    "storage_free_bytes": 500,
    "cpu_temperature_c": 50.0,
    "network": {"interfaces": [{"name": "eth0", "operstate": "up"}]},
}


def make_assistant(directory=None, health=None, vehicle=None):
    return AssistantService(
        Path(directory) if directory else None,
        None,
        lambda: health or HEALTHY,
        INITIAL_CAPABILITIES,
        vehicle=vehicle,
    )


class FeatureToggleTests(unittest.TestCase):
    def test_disabled_by_default_and_toggles(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            store = FeatureStore(Path(d))
            self.assertFalse(store.is_enabled("assistant"))
            store.set_enabled("assistant", True)
            self.assertTrue(store.is_enabled("assistant"))
            store.set_enabled("assistant", False)
            self.assertFalse(store.is_enabled("assistant"))

    def test_corrupt_state_means_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "features.json").write_text('{"assistant": "yes"')
            self.assertFalse(FeatureStore(Path(d)).is_enabled("assistant"))
            (Path(d) / "features.json").write_text('{"assistant": "yes"}')
            self.assertFalse(FeatureStore(Path(d)).is_enabled("assistant"))

    def test_cli_enable_disable(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(
                features_main(["--state-directory", d, "enable", "assistant"]), 0
            )
            self.assertTrue(FeatureStore(Path(d)).is_enabled("assistant"))
            self.assertEqual(
                features_main(["--state-directory", d, "disable", "assistant"]), 0
            )
            self.assertFalse(FeatureStore(Path(d)).is_enabled("assistant"))

    def test_unknown_feature_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                FeatureStore(Path(d)).set_enabled("nope", True)


class PermissionTests(unittest.TestCase):
    def test_recording_changes_cannot_be_weak(self) -> None:
        with self.assertRaises(ValueError):
            validate_permission("stop_recording", Permission.AUTOMATIC)
        with self.assertRaises(ValueError):
            validate_permission("reboot_pi", Permission.ASK)
        validate_permission("recording_status", Permission.AUTOMATIC)

    def test_strict_tools_need_gate(self) -> None:
        with self.assertRaises(ValueError):
            ToolRegistry().register(
                Tool("set_recording_mode", "x", Permission.STRICT, lambda a: {})
            )


class OrchestratorTests(unittest.TestCase):
    def test_recording_status_is_not_invented(self) -> None:
        reply = make_assistant().orchestrator.handle("Kenpi, are you recording?")
        self.assertIn("can't verify", reply.message)

    def test_stop_recording_is_always_refused(self) -> None:
        for approved in (False, True):
            reply = make_assistant().orchestrator.handle(
                "stop recording", approved=approved
            )
            self.assertFalse(reply.outcomes[0].executed)

    def test_preview_blocked_without_evidence(self) -> None:
        reply = make_assistant().orchestrator.handle("show me the camera")
        self.assertFalse(reply.outcomes[0].executed)
        self.assertIn("not verified", reply.message)

    def test_clarification_flow(self) -> None:
        orchestrator = make_assistant().orchestrator
        first = orchestrator.handle("show me yesterday's recording", "s1")
        self.assertEqual(first.message, "Which camera?")
        second = orchestrator.handle("rear", "s1")
        self.assertEqual(
            second.plan.steps[0].args, {"day": "yesterday", "camera": "rear"}
        )

    def test_preferences_set_and_forget(self) -> None:
        assistant = make_assistant()
        assistant.orchestrator.handle("set the speech speed to 0.9")
        self.assertEqual(assistant.memory.get("speech_speed"), "0.9")
        assistant.orchestrator.handle("forget my preferences")
        self.assertEqual(assistant.memory.all(), {})

    def test_provider_output_is_validated(self) -> None:
        registry = ToolRegistry()
        registry.register(
            Tool(
                "ping",
                "x",
                Permission.AUTOMATIC,
                lambda a: {"message": "pong"},
                {"n": int},
            )
        )

        class Provider:
            def propose(self, text, tools, history=()):
                return [
                    {"tool": "rm_rf"},
                    {"tool": "ping", "args": {"n": "x"}},
                    {"tool": "ping", "args": {"n": 1}},
                ]

        plan = Planner(registry, Provider()).plan("gibberish")
        self.assertEqual([s.tool for s in plan.steps], ["ping"])

    def test_non_user_initiated_requires_automatic(self) -> None:
        registry = ToolRegistry()
        registry.register(Tool("restart_voice", "x", Permission.ASK, lambda a: {}))
        from kenpi.intelligence.tools import PolicyEngine

        tool = registry.get("restart_voice")
        self.assertFalse(
            PolicyEngine().decide(tool, approved=True, user_initiated=False).allowed
        )
        self.assertTrue(PolicyEngine().decide(tool, approved=True).allowed)
        self.assertTrue(PolicyEngine().decide(tool).needs_approval)


class DiagnosticsTests(unittest.TestCase):
    def test_healthy_host_has_no_faults_and_camera_unverified(self) -> None:
        healthy = {
            "load_1m": 0.1,
            "cpu_count": 4,
            "swap_total_bytes": 0,
            "swap_used_bytes": 0,
            "throttled_flags": 0,
            "usb_devices": [],
            "readonly_mounts": [],
            "board": {
                "model": "Raspberry Pi 5 Model B",
                "psu_max_current_ma": 5000,
                "fan_present": True,
            },
        }
        with unittest.mock.patch("kenpi.assistant.collect_system", lambda: healthy):
            report = make_assistant().diagnostics.run()
        self.assertEqual(report.fault_ids() - {"voice.no_engine"}, set())
        self.assertEqual(report.subsystem_status["host"], "ok")
        self.assertEqual(report.subsystem_status["camera"], "unknown")
        self.assertIn("Not verified", report.render_text())

    def test_detects_hot_low_memory_with_corroboration(self) -> None:
        health = {**HEALTHY, "memory_available_bytes": 20, "cpu_temperature_c": 88.0}
        report = make_assistant(health=health).diagnostics.run()
        self.assertTrue({"host.memory_low", "host.thermal"} <= report.fault_ids())
        self.assertGreater(report.causes[0].confidence, 0.75)

    def test_lost_vehicle_link(self) -> None:
        link = {
            "link_state": "lost",
            "last_message_age_seconds": 30.0,
            "rejected_frames": 2,
        }
        report = make_assistant(vehicle=lambda: link).diagnostics.run()
        self.assertIn("vehicle.heartbeat", report.fault_ids())
        self.assertIn("vehicle.frames_rejected", report.fault_ids())

    def test_failing_check_does_not_abort(self) -> None:
        def broken():
            raise RuntimeError("x")

        report = make_assistant(health=None).diagnostics.run()
        self.assertTrue(report.results)
        assistant = AssistantService(None, None, broken, INITIAL_CAPABILITIES)
        report = assistant.diagnostics.run()
        self.assertTrue(any(r.status is Status.UNKNOWN for r in report.results))

    def test_log_analysis(self) -> None:
        lines = [
            "2026-11-07 10:00:00,123 ERROR kenpi.x Under-voltage detected!",
            "2026-11-07 10:00:05,000 WARNING kenpi.y request timed out",
            "garbage line",
        ]
        analysis = analyze(lines)
        self.assertEqual(analysis.signatures["undervoltage"], 1)
        self.assertEqual(analysis.error_count, 1)

    def test_guide_includes_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            guide = make_assistant(d).diagnostics.guide("host.storage_low")
        levels = {s["action_id"]: s["permission"] for s in guide["steps"]}
        self.assertEqual(levels["clear_assistant_cache"], "ASK")

    def test_remediation_needs_approval_and_is_verified(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            assistant = make_assistant(d)
            cache = Path(d) / "cache"
            cache.mkdir()
            (cache / "a.wav").write_bytes(b"x")
            result, report = assistant.diagnostics.remediate("clear_assistant_cache")
            self.assertFalse(result.executed)
            self.assertTrue(cache.exists())
            result, report = assistant.diagnostics.remediate(
                "clear_assistant_cache", approved=True
            )
            self.assertTrue(result.executed)
            self.assertFalse(cache.exists())
            self.assertIsNotNone(report)
            kinds = [r["kind"] for r in assistant.diagnostics.history.recent()]
            self.assertIn("verification", kinds)

    def test_cache_precondition_blocks_when_nothing_to_clear(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            assistant = make_assistant(d)
            result, report = assistant.diagnostics.remediate(
                "clear_assistant_cache", approved=True
            )
            self.assertFalse(result.executed)
            self.assertIn("precondition", result.detail)
            self.assertIsNone(report)

    def test_cache_symlink_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as t:
            assistant = make_assistant(d)
            (Path(t) / "keep.txt").write_text("x")
            (Path(d) / "cache").symlink_to(t)
            result, _ = assistant.diagnostics.remediate(
                "clear_assistant_cache", approved=True
            )
            self.assertFalse(result.executed)
            self.assertTrue((Path(t) / "keep.txt").exists())

    def test_verification_records_targeted_faults(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            assistant = make_assistant(d)
            (Path(d) / "cache").mkdir()
            assistant.diagnostics.remediate("clear_assistant_cache", approved=True)
            last = [
                r
                for r in assistant.diagnostics.history.recent()
                if r["kind"] == "verification"
            ][-1]
            self.assertIn("targeted_still_present", last)

    def test_cannot_register_weak_recording_action(self) -> None:
        with self.assertRaises(ValueError):
            RemediationRegistry().register(
                RemediationAction("disable_recording", "x", Permission.ASK, lambda: "")
            )

    def test_never_actions_do_not_run(self) -> None:
        registry = RemediationRegistry()
        registry.register(
            RemediationAction("stop_recording", "x", Permission.NEVER, lambda: "ran")
        )
        self.assertFalse(registry.execute("stop_recording", approved=True).executed)


class VoiceTests(unittest.TestCase):
    def test_custom_voice_requires_consent(self) -> None:
        with self.assertRaises(ValueError):
            VoiceProfile(
                "mine", engine="openvoice_v2", speaker_embedding="mine/speaker.pth"
            )
        profile = VoiceProfile(
            "mine",
            engine="openvoice_v2",
            speaker_embedding="mine/speaker.pth",
            consent=new_consent("Me", "My own voice"),
        )
        self.assertTrue(profile.describe()["has_custom_voice"])
        self.assertNotIn("speaker_embedding", profile.describe())

    def test_profile_validation(self) -> None:
        for bad in (
            {"speed": 5},
            {"language": "xx"},
            {"speaker_embedding": "../x", "consent": new_consent("a", "b")},
        ):
            with self.assertRaises(ValueError):
                VoiceProfile("p", **bad)

    def test_profile_store_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "p.json"
            store = ProfileStore(path)
            store.add(VoiceProfile("calm", style="calm", consent=None))
            store.activate("calm")
            self.assertEqual(ProfileStore(path).active.id, "calm")
            store.delete("calm")
            self.assertEqual(ProfileStore(path).active.id, "kenpi_default")
            with self.assertRaises(ValueError):
                store.delete("kenpi_default")

    def test_fallback_to_melotts_when_openvoice_down(self) -> None:
        class Down:
            id = "openvoice_v2"
            supports_cloning = True

            def availability(self):
                return False, "down"

            def synthesize(self, request):
                raise EngineUnavailable("down")

        class Fake:
            id = "melotts"
            supports_cloning = False

            def availability(self):
                return True, "ready"

            def synthesize(self, request):
                return b"RIFF"

        engines = EngineRegistry()
        engines.register(Down())
        engines.register(Fake())
        profiles = ProfileStore()
        profiles.add(VoiceProfile("ov", engine="openvoice_v2"))
        profiles.activate("ov")
        result = VoiceService(engines, profiles).speak("hello")
        self.assertEqual(
            (result.engine, result.fell_back, result.audio), ("melotts", True, b"RIFF")
        )

    def test_no_engine_reports_reason_without_raising(self) -> None:
        result = make_assistant().voice.speak("hello")
        self.assertIsNone(result.audio)

    def test_openvoice_remote_requires_opt_in(self) -> None:
        with self.assertRaises(ValueError):
            OpenVoiceEngine("http://192.168.1.9:9000")
        OpenVoiceEngine("http://192.168.1.9:9000", allow_remote=True)
        OpenVoiceEngine("http://127.0.0.1:9000")

    def test_catalog_checksum_install(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "src.bin"
            src.write_bytes(b"model")
            digest = sha256_of(src)
            entry = ModelEntry(
                "m",
                "1",
                "tts",
                "pi",
                1,
                ("en",),
                True,
                "MIT",
                True,
                "x",
                digest,
                "m/model.bin",
            )
            catalog = ModelCatalog(Path(d) / "models", (entry,))
            with self.assertRaises(PermissionError):
                catalog.install_from_file("m", src, approved=False)
            self.assertEqual(
                catalog.install_from_file("m", src, approved=True), "installed"
            )
            self.assertEqual(catalog.describe()[0]["status"], "installed")
            (Path(d) / "models" / "m" / "model.bin").write_bytes(b"tampered")
            self.assertEqual(catalog.describe()[0]["status"], "failed")
            src.write_bytes(b"other")
            with self.assertRaises(ValueError):
                catalog.install_from_file("m", src, approved=True)

    def test_default_catalog_cannot_install_unpinned(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "f"
            src.write_bytes(b"x")
            with self.assertRaises(ValueError):
                ModelCatalog(Path(d) / "m").install_from_file(
                    "whisper.cpp", src, approved=True
                )


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def _get(self, service, path):
        opener = build_opener(ProxyHandler({}))

        def fetch():
            try:
                with opener.open(
                    f"http://127.0.0.1:{service.bound_port}{path}", timeout=5
                ) as r:
                    return r.status, json.loads(r.read())
            except HTTPError as error:
                return error.code, json.loads(error.read())

        return await asyncio.to_thread(fetch)

    async def test_disabled_feature_has_no_routes(self) -> None:
        service = KenpiApiService(
            port=0, features_provider=lambda: {"assistant": {"enabled": False}}
        )
        await service.start()
        try:
            self.assertEqual((await self._get(service, "/api/v1/voice/status"))[0], 404)
            status, body = await self._get(service, "/api/v1/features")
            self.assertFalse(body["features"]["assistant"]["enabled"])
            self.assertEqual((await self._get(service, "/api/v1/diagnostics"))[0], 200)
        finally:
            await service.stop()

    async def test_enabled_routes_are_read_only(self) -> None:
        assistant = make_assistant()
        service = KenpiApiService(port=0, assistant=assistant)
        await service.start()
        try:
            for path in (
                "/api/v1/voice/status",
                "/api/v1/voice/engines",
                "/api/v1/voice/catalog",
                "/api/v1/voice/profiles",
                "/api/v1/voice/characters",
                "/api/v1/intelligence/tools",
                "/api/v1/diagnostics/report",
            ):
                self.assertEqual((await self._get(service, path))[0], 200, path)
            status, body = await self._get(
                service, "/api/v1/intelligence/plan?text=stop%20recording"
            )
            self.assertEqual(body["plan"]["steps"][0]["tool"], "stop_recording")
            self.assertFalse(body["executed"])
            self.assertEqual((await self._get(service, "/api/v1/voice/nope"))[0], 404)
        finally:
            await service.stop()


if __name__ == "__main__":
    unittest.main()

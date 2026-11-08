import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener

from kenpi.models_cli import main as models_main
from kenpi.voice.characters import (
    CHARACTERS,
    DEFAULT_CHARACTER,
    CharacterVoice,
    get_character,
    resolve_character,
)
from kenpi.voice.catalog import ModelCatalog, sha256_of
from kenpi.voice.engines import (
    EngineRegistry,
    EngineUnavailable,
    OpenVoiceEngine,
    MeloEngine,
    SynthesisRequest,
)
from kenpi.voice.pipeline_server import (
    BadRequest,
    resolve_embedding,
    serve,
    validate_synthesis,
)
from kenpi.voice.profiles import ProfileStore, character_profile_id
from kenpi.voice.service import VoiceService
from tests.test_assistant import make_assistant


class FakeBackend:
    def __init__(self) -> None:
        self.calls = []

    def ready(self):
        return True, "ready"

    def synthesize(self, text, language, speed, base_speaker, embedding):
        self.calls.append((text, language, speed, base_speaker, embedding))
        return b"RIFFfake"

    def extract(self, wav):
        return b"EMBED" + wav[:4]


class CharacterTests(unittest.TestCase):
    def test_four_characters_brooklyn_default(self) -> None:
        self.assertEqual(len(CHARACTERS), 4)
        self.assertEqual(DEFAULT_CHARACTER, "brooklyn_gentleman")
        self.assertEqual(make_assistant().character.id, DEFAULT_CHARACTER)
        self.assertEqual(len({c.accent for c in CHARACTERS}), 4)

    def test_resolve_by_spoken_name(self) -> None:
        self.assertEqual(
            resolve_character("switch to the professor").id, "english_professor"
        )
        self.assertEqual(resolve_character("use Madame Colette").id, "madame_colette")
        self.assertIsNone(resolve_character("banana"))

    def test_rendering_keeps_facts_verbatim(self) -> None:
        for character in CHARACTERS:
            voice = CharacterVoice(character)
            for kind in ("greeting", "diagnostic_fault", "refusal"):
                message = "Storage is 5 percent free."
                self.assertTrue(voice.render(kind, message).endswith(message))

    def test_every_character_covers_every_situation(self) -> None:
        from kenpi.voice.characters import PHRASE_KINDS

        for character in CHARACTERS:
            self.assertEqual(set(PHRASE_KINDS), set(character.phrases))

    def test_switch_by_voice_keeps_history_and_context(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            assistant = make_assistant(d)
            first = assistant.orchestrator.handle("show recordings today", "s1")
            self.assertIn("camera", first.message.lower())
            assistant.set_character("english_professor")
            self.assertEqual(assistant.character.id, "english_professor")
            # The pending question from before the switch is still answerable.
            done = assistant.orchestrator.handle("front", "s1")
            self.assertEqual(done.plan.steps[0].tool, "list_recordings")
            reply = assistant.orchestrator.handle("switch to the southern fixer", "s1")
            self.assertIn("Southern Fixer", reply.message)
            # Persisted across restarts.
            self.assertEqual(make_assistant(d).character.id, "southern_fixer")

    def test_unknown_character_asks(self) -> None:
        assistant = make_assistant()
        assistant.orchestrator.handle("switch to the banana character")
        self.assertEqual(assistant.character.id, DEFAULT_CHARACTER)

    def test_diagnostics_use_character_wording_without_changing_facts(self) -> None:
        assistant = make_assistant(health={"cpu_temperature_c": 95.0})
        plain = assistant.diagnostics.run().render_text().splitlines()[0]
        assistant.set_character("madame_colette")
        spoken = assistant.orchestrator.handle("run diagnostics").message
        self.assertIn(plain, spoken)

    def test_refusal_stays_a_refusal_in_every_voice(self) -> None:
        assistant = make_assistant()
        for character in CHARACTERS:
            assistant.set_character(character.id)
            reply = assistant.orchestrator.handle("stop recording")
            self.assertFalse(reply.outcomes[0].executed)
            self.assertIn("can't do that", reply.message)

    def test_notification_phrasing(self) -> None:
        assistant = make_assistant()
        assistant.set_character("southern_fixer")
        text = assistant.say("notify_warning", "Storage is low.")
        self.assertTrue(text.endswith("Storage is low."))
        self.assertNotEqual(text, "Storage is low.")

    def test_unknown_id_rejected(self) -> None:
        with self.assertRaises(ValueError):
            get_character("nope")
        with self.assertRaises(ValueError):
            make_assistant().set_character("nope")


class CharacterVoiceRoutingTests(unittest.TestCase):
    def test_each_character_gets_its_own_profile_and_base_speaker(self) -> None:
        profiles = ProfileStore()
        speakers = {
            profiles.get(character_profile_id(c.id)).base_speaker for c in CHARACTERS
        }
        self.assertIn("EN-BR", speakers)
        self.assertEqual(profiles.get("char_southern_fixer").speed, 0.92)

    def test_request_carries_base_speaker(self) -> None:
        seen = []

        class Fake:
            id = "openvoice_v2"
            supports_cloning = True

            def availability(self):
                return True, "ready"

            def synthesize(self, request):
                seen.append(request)
                return b"OK"

        engines = EngineRegistry()
        engines.register(Fake())
        service = VoiceService(engines, ProfileStore())
        result = service.speak("hi", "english_professor")
        self.assertEqual(result.engine, "openvoice_v2")
        self.assertEqual(seen[0].base_speaker, "EN-BR")

    def test_falls_back_to_melotts(self) -> None:
        seen = []

        class Down:
            id = "openvoice_v2"
            supports_cloning = True

            def availability(self):
                return False, "down"

            def synthesize(self, request):
                raise EngineUnavailable("down")

        class Melo:
            id = "melotts"
            supports_cloning = False

            def availability(self):
                return True, "ready"

            def synthesize(self, request):
                seen.append(request)
                return b"P"

        engines = EngineRegistry()
        engines.register(Down())
        engines.register(Melo())
        result = VoiceService(engines, ProfileStore()).speak("hi", "madame_colette")
        self.assertEqual((result.engine, result.fell_back), ("melotts", True))

    def test_melo_engine_never_sends_an_embedding(self) -> None:
        sent = []
        engine = MeloEngine("http://127.0.0.1:1")
        engine._opener = type(
            "O",
            (),
            {
                "open": lambda self, req, timeout=0: sent.append(req)
                or (_ for _ in ()).throw(OSError())
            },
        )()
        with self.assertRaises(EngineUnavailable):
            engine.synthesize(
                SynthesisRequest(
                    "hi", speaker_embedding=Path("/x"), base_speaker="EN-BR"
                )
            )
        body = json.loads(sent[0].data)
        self.assertIsNone(body["speaker_embedding"])
        self.assertEqual(body["base_speaker"], "EN-BR")

    def test_enroll_requires_authorization_and_is_reversible(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            assistant = make_assistant(d)

            class Extractor:
                id = "openvoice_v2"
                supports_cloning = True

                def availability(self):
                    return True, "ready"

                def synthesize(self, request):
                    return b"X"

                def extract_embedding(self, wav):
                    return b"EMB"

            assistant.voice.engines._engines["openvoice_v2"] = Extractor()
            with self.assertRaises(ValueError):
                assistant.enroll_character_voice("southern_fixer", b"w", "Bob", " ")
            assistant.enroll_character_voice(
                "southern_fixer", b"w", "Bob", "Recorded by me with permission"
            )
            profile = assistant.voice.profiles.get("char_southern_fixer")
            self.assertEqual(profile.speaker_embedding, "southern_fixer/speaker.pth")
            self.assertEqual(profile.base_speaker, "EN-US")
            # Survives restart.
            reloaded = make_assistant(d).voice.profiles.get("char_southern_fixer")
            self.assertIsNotNone(reloaded.speaker_embedding)
            assistant.remove_character_voice("southern_fixer")
            self.assertIsNone(
                make_assistant(d)
                .voice.profiles.get("char_southern_fixer")
                .speaker_embedding
            )


class PinTests(unittest.TestCase):
    def test_pin_then_install_and_tamper_detection(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            models = Path(d) / "models"
            src = Path(d) / "config.json"
            src.write_text("{}")
            catalog = ModelCatalog(models)
            with self.assertRaises(PermissionError):
                catalog.pin("melotts-english-config", src, accept_license=False)
            digest = catalog.pin("melotts-english-config", src, accept_license=True)
            self.assertEqual(digest, sha256_of(src))
            fresh = ModelCatalog(models)
            self.assertEqual(
                catalog.install_from_file("melotts-english-config", src, True),
                "installed",
            )
            status = {m["id"]: m for m in fresh.describe()}["melotts-english-config"]
            self.assertEqual((status["status"], status["pinned"]), ("installed", True))
            (models / "melo/EN/config.json").write_text("tampered")
            status = {m["id"]: m for m in fresh.describe()}["melotts-english-config"]
            self.assertEqual(status["status"], "failed")

    def test_wrong_file_rejected_after_pin(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            catalog = ModelCatalog(Path(d) / "m")
            good, bad = Path(d) / "good", Path(d) / "bad"
            good.write_bytes(b"a")
            bad.write_bytes(b"b")
            catalog.pin("openvoice-v2-converter-config", good, True)
            with self.assertRaises(ValueError):
                catalog.install_from_file("openvoice-v2-converter-config", bad, True)

    def test_only_mit_candidates_in_catalog(self) -> None:
        from kenpi.voice.catalog import DEFAULT_ENTRIES

        ids = {e.id for e in DEFAULT_ENTRIES}
        self.assertFalse(
            {i for i in ids if "piper" in i or i in ("kokoro", "sherpa-onnx")}
        )
        self.assertIn("chatterbox-turbo", ids)
        self.assertTrue(all(e.license.startswith("MIT") for e in DEFAULT_ENTRIES))

    def test_no_fabricated_checksums_ship(self) -> None:
        from kenpi.voice.catalog import DEFAULT_ENTRIES

        self.assertTrue(all(e.sha256 == "" for e in DEFAULT_ENTRIES))

    def test_pins_file_ignores_junk(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "pins.json").write_text(
                json.dumps({"melotts-english": "xyz", "nonexistent": "a" * 64})
            )
            self.assertFalse(any(m["pinned"] for m in ModelCatalog(Path(d)).describe()))

    def test_cli(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "f"
            src.write_bytes(b"1")
            base = ["--state-directory", d]
            self.assertEqual(models_main([*base, "pin", "melotts-french", str(src)]), 1)
            self.assertEqual(
                models_main(
                    [*base, "pin", "melotts-french", str(src), "--accept-license"]
                ),
                0,
            )
            self.assertEqual(models_main([*base, "list"]), 0)


class PipelineServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.backend = FakeBackend()
        self.server = serve(self.backend, self.root, "127.0.0.1", 0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.opener = build_opener(ProxyHandler({}))

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def _post(self, path, body, content_type="application/json"):
        from urllib.error import HTTPError

        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        try:
            with self.opener.open(
                Request(self.url + path, data, {"Content-Type": content_type})
            ) as r:
                return r.status, r.read()
        except HTTPError as error:
            return error.code, error.read()

    def test_engine_client_round_trip(self) -> None:
        engine = OpenVoiceEngine(self.url)
        self.assertEqual(engine.availability(), (True, "ready"))
        emb = self.root / "e.pth"
        emb.write_bytes(b"e")
        audio = engine.synthesize(
            SynthesisRequest("hello", "en", "neutral", 0.9, emb, base_speaker="EN-BR")
        )
        self.assertEqual(audio, b"RIFFfake")
        self.assertEqual(self.backend.calls[0][3:], ("EN-BR", emb.resolve()))
        self.assertEqual(engine.extract_embedding(b"WAVE...."), b"EMBEDWAVE")

    def test_embedding_outside_root_rejected(self) -> None:
        outside = Path(tempfile.mkdtemp()) / "e.pth"
        outside.write_bytes(b"e")
        status, _ = self._post(
            "/synthesize", {"text": "hi", "speaker_embedding": str(outside)}
        )
        self.assertEqual(status, 400)
        self.assertEqual(self.backend.calls, [])

    def test_validation(self) -> None:
        for bad in (
            {"text": ""},
            {"text": "x", "language": "xx"},
            {"text": "x", "speed": 9},
            {"text": "x", "speed": True},
            {"text": "x", "base_speaker": "../x"},
            [],
        ):
            with self.assertRaises(BadRequest):
                validate_synthesis(bad)
        with self.assertRaises(BadRequest):
            resolve_embedding(self.root, 5)

    def test_unknown_path_and_bad_json(self) -> None:
        self.assertEqual(self._post("/nope", {})[0], 404)
        self.assertEqual(self._post("/synthesize", b"{not json")[0], 400)

    def test_backend_failure_is_500_not_a_crash(self) -> None:
        def boom(*args):
            raise RuntimeError("secret detail")

        self.backend.synthesize = boom
        status, body = self._post("/synthesize", {"text": "hi"})
        self.assertEqual(status, 500)
        self.assertNotIn(b"secret", body)

    def test_loopback_only(self) -> None:
        with self.assertRaises(ValueError):
            serve(self.backend, self.root, "0.0.0.0", 0)


if __name__ == "__main__":
    unittest.main()


class SarcasmTests(unittest.TestCase):
    def test_every_character_has_sarcasm_for_each_kind(self):
        from kenpi.voice.characters import SARCASM, SARCASM_KINDS

        for c in CHARACTERS:
            self.assertEqual(set(SARCASM[c.id]), set(SARCASM_KINDS), c.id)

    def test_no_sarcasm_on_faults_or_warnings(self):
        from kenpi.voice.characters import SARCASM_KINDS

        self.assertNotIn("diagnostic_fault", SARCASM_KINDS)
        self.assertNotIn("notify_warning", SARCASM_KINDS)

    def test_sarcasm_used_and_facts_preserved(self):
        for c in CHARACTERS:
            voice = CharacterVoice(c)
            seen = {voice.line("refusal") for _ in range(6)}
            self.assertTrue(
                seen
                & set(
                    __import__("kenpi.voice.characters", fromlist=["SARCASM"]).SARCASM[
                        c.id
                    ]["refusal"]
                )
            )
            self.assertTrue(voice.render("refusal", "Fact.").endswith("Fact."))

    def test_sarcasm_can_be_disabled(self):
        from kenpi.voice.characters import SARCASM

        for c in CHARACTERS:
            voice = CharacterVoice(c, sarcasm=False)
            lines = {voice.line("refusal") for _ in range(6)}
            self.assertFalse(lines & set(SARCASM[c.id]["refusal"]))

    def test_assistant_toggle(self):
        assistant = make_assistant()
        self.assertTrue(assistant.character_voice().sarcasm)
        assistant.set_sarcasm(False)
        self.assertFalse(assistant.character_voice().sarcasm)


class SwearingTests(unittest.TestCase):
    def test_every_character_has_levels_and_lines(self):
        from kenpi.voice.characters import LEVELS, SWEARING, SWEAR_KINDS

        for c in CHARACTERS:
            self.assertIn(c.id, LEVELS)
            self.assertTrue(all(0 <= v <= 3 for v in LEVELS[c.id]))
            self.assertTrue(SWEARING[c.id])
            self.assertTrue(set(SWEARING[c.id]) <= set(SWEAR_KINDS))

    def test_levels_differ_between_characters(self):
        from kenpi.voice.characters import LEVELS

        self.assertGreater(len({v for v in LEVELS.values()}), 1)

    def test_swearing_off_by_default_and_never_on_warnings(self):
        from kenpi.voice.characters import SWEARING

        for c in CHARACTERS:
            off = CharacterVoice(c)
            self.assertFalse(off.swearing)
            lines = {off.line("diagnostic_fault") for _ in range(6)}
            self.assertFalse(lines & set(SWEARING[c.id].get("diagnostic_fault", ())))
            self.assertNotIn("notify_warning", SWEARING[c.id])

    def test_swearing_appears_when_on_and_facts_preserved(self):
        from kenpi.voice.characters import SWEARING

        for c in CHARACTERS:
            voice = CharacterVoice(c, sarcasm=False, swearing=True)
            seen = {voice.line("refusal") for _ in range(6)}
            self.assertTrue(seen & set(SWEARING[c.id]["refusal"]), c.id)
            self.assertTrue(voice.render("refusal", "Fact.").endswith("Fact."))

    def test_user_cannot_change_levels(self):
        from kenpi.intelligence.memory import PreferenceMemory

        memory = PreferenceMemory()
        for key in ("sarcasm_level", "swearing_level", "levels"):
            with self.assertRaises(ValueError):
                memory.set(key, "3")

    def test_assistant_swearing_toggle_persists(self):
        assistant = make_assistant()
        self.assertFalse(assistant.character_voice().swearing)
        assistant.set_swearing(True)
        self.assertTrue(assistant.character_voice().swearing)
        assistant.set_swearing(False)
        self.assertFalse(assistant.character_voice().swearing)

    def test_user_profanity_does_not_change_the_plan(self):
        assistant = make_assistant()
        clean = assistant.orchestrator.handle("is the camera recording", "s1")
        rude = assistant.orchestrator.handle(
            "is the camera fucking recording, dammit", "s2"
        )
        self.assertTrue(clean.outcomes)
        self.assertEqual(
            [o.tool for o in clean.outcomes], [o.tool for o in rude.outcomes]
        )

"""Optional assistant subsystem: voice, intent planning and Diagnostics & AI Support.

Everything here is subordinate to the core service. Failures are contained so camera
recording and the status API are never affected.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import shutil
import time
import tomllib
from collections import deque
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Awaitable, Callable

from kenpi.companion import CompanionRegistry, redact
from kenpi.core.events import EventBus, EventType
from kenpi.core.permissions import Permission
from kenpi.diagnostics.system import collect_network, collect_system
from kenpi.diagnostics.ingest import JournalUnavailable, LogStore, read_journal
from kenpi.diagnostics.engine import DiagnosticsEngine
from kenpi.diagnostics.history import DiagnosticHistory
from kenpi.diagnostics.models import DiagnosticContext, Status
from kenpi.diagnostics.remediation import RemediationAction, RemediationRegistry
from kenpi.intelligence.memory import ConversationStore, PreferenceMemory, SessionStore
from kenpi.intelligence.orchestrator import Orchestrator
from kenpi.intelligence.provider import ChatCompletionsProvider
from kenpi.intelligence.planner import Planner
from kenpi.intelligence.tools import PolicyEngine, Tool, ToolRegistry, cancelled
from kenpi.media.library import LocalMediaLibrary
from kenpi.voice.catalog import ModelCatalog
from kenpi.voice.characters import (
    CHARACTERS,
    DEFAULT_CHARACTER,
    Character,
    CharacterVoice,
    get_character,
)
from kenpi.voice.engines import (
    ChatterboxEngine,
    EngineRegistry,
    MeloEngine,
    OpenVoiceEngine,
    VoiceboxEngine,
)
from kenpi.voice.profiles import (
    ProfileStore,
    VoiceProfile,
    character_profile_id,
    new_consent,
)
from kenpi.voice.service import VoiceService

logger = logging.getLogger(__name__)


class LogRing(logging.Handler):
    """Keeps recent in-process log lines as diagnostic evidence."""

    def __init__(self, capacity: int = 2000) -> None:
        super().__init__()
        self._lines: deque[str] = deque(maxlen=capacity)
        self.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._lines.append(self.format(record))
        except Exception:
            self.handleError(record)

    def lines(self) -> list[str]:
        return list(self._lines)


@dataclass(frozen=True, slots=True)
class AssistantSettings:
    openvoice_url: str = ""
    openvoice_allow_remote: bool = False
    chatterbox_url: str = ""
    voicebox_url: str = ""
    voicebox_allow_remote: bool = False
    voicebox_default_profile: str = ""
    # character id -> Voicebox profile name; needs voicebox_attestation (authorization to use them)
    voicebox_profiles: dict[str, str] = field(default_factory=dict)
    voicebox_attestation: str = ""
    llm_url: str = ""
    journal_units: tuple[str, ...] = ("kenpi-status.service",)
    llm_model: str = ""
    llm_timeout_seconds: float = 8.0
    llm_allow_remote: bool = False
    llm_api_key: str = ""
    whisper_binary: str = "whisper-cli"
    whisper_model: str = ""
    wake_words: tuple[str, ...] = ("hey kenpi",)
    audio_device: str = ""

    @classmethod
    def load(cls, path: Path) -> "AssistantSettings":
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            return cls()
        allowed = set(cls.__dataclass_fields__)
        try:
            return cls(**{k: v for k, v in data.items() if k in allowed})
        except TypeError:
            return cls()


# Read-only SAE J1979 mode 01 PIDs that kenpi.vehicle.kline can decode.
VEHICLE_QUANTITIES = {
    "coolant_temperature": 0x05,
    "engine_speed": 0x0C,
    "vehicle_speed": 0x0D,
}


def _capability_evidence(capabilities: tuple[Any, ...]) -> dict[str, dict[str, str]]:
    return {
        c.name: {
            "state": c.state.value,
            "evidence": c.evidence.name,
            "detail": c.detail,
        }
        for c in capabilities
    }


class AssistantService:
    """ManagedService wrapper. start/stop never raise, so the core service stays up."""

    def __init__(
        self,
        data_dir: Path | None,
        events: EventBus | None,
        host_health: Callable[[], dict[str, Any]],
        capabilities: tuple[Any, ...],
        coordinator: Callable[[], dict[str, Any]] | None = None,
        vehicle: Callable[[], dict[str, Any] | None] | None = None,
        diagnose: Callable[..., Awaitable[dict[str, Any]]] | None = None,
        media_library: LocalMediaLibrary | None = None,
        settings: AssistantSettings | None = None,
    ) -> None:
        self._events = events
        self._data_dir = data_dir
        self._vehicle = vehicle
        self._diagnose = diagnose
        self._loop: asyncio.AbstractEventLoop | None = None
        self._log_ring = LogRing()
        self.log_store = LogStore()
        self._running = False
        settings = settings or (
            AssistantSettings.load(data_dir / "assistant.toml")
            if data_dir
            else AssistantSettings()
        )
        models_dir = data_dir / "models" if data_dir else None
        self._journal_units = tuple(settings.journal_units)

        engines = EngineRegistry()
        for engine_class, url in (
            (OpenVoiceEngine, settings.openvoice_url),
            (MeloEngine, settings.openvoice_url),
            (ChatterboxEngine, settings.chatterbox_url),
        ):
            try:
                engines.register(engine_class(url, settings.openvoice_allow_remote))
            except ValueError:
                logger.warning(
                    "Invalid %s URL; engine left unconfigured", engine_class.id
                )
                engines.register(engine_class(""))
        try:
            engines.register(
                VoiceboxEngine(
                    settings.voicebox_url,
                    settings.voicebox_allow_remote,
                    default_profile=settings.voicebox_default_profile,
                )
            )
        except ValueError:
            logger.warning("Invalid Voicebox URL; engine left unconfigured")
            engines.register(VoiceboxEngine(""))
        self.voice = VoiceService(
            engines,
            ProfileStore(data_dir / "voice" / "profiles.json" if data_dir else None),
            data_dir / "voice" if data_dir else None,
        )
        self._map_voicebox_profiles(settings)
        self.catalog = ModelCatalog(models_dir)
        self.companion = CompanionRegistry()
        self.memory = PreferenceMemory(
            data_dir / "preferences.json" if data_dir else None
        )

        evidence = lambda: _capability_evidence(capabilities)  # noqa: E731
        context = DiagnosticContext(
            host_health=host_health,
            coordinator=coordinator,
            vehicle=vehicle,
            capabilities=evidence,
            voice_status=self.voice.status,
            companion=self.companion.evidence,
            system=collect_system,
            network=lambda: collect_network(
                cert_path=data_dir.parent / "gateway" / "gateway.pem"
                if data_dir
                else None
            ),
            log_lines=self._observed_logs,
        )
        remediation = RemediationRegistry()
        if data_dir is not None:
            remediation.register(
                RemediationAction(
                    "clear_assistant_cache",
                    "Delete cached synthesized audio",
                    Permission.ASK,
                    self._clear_cache,
                )
            )
        self.diagnostics = DiagnosticsEngine(
            context,
            remediation=remediation,
            history=DiagnosticHistory(
                data_dir / "diagnostic-history.jsonl" if data_dir else None
            ),
        )
        self.tools = ToolRegistry()
        self._register_tools(host_health, evidence, media_library)
        self.model_provider = self._build_provider(settings)
        self.orchestrator = Orchestrator(
            self.tools,
            Planner(self.tools, self.model_provider),
            PolicyEngine(),
            SessionStore(),
            events,
            character=self.character_voice,
            conversations=ConversationStore(
                data_dir / "conversations.json" if data_dir else None
            ),
        )
        self._voices: dict[str, CharacterVoice] = {}

    def _map_voicebox_profiles(self, settings: AssistantSettings) -> None:
        """Route characters to operator-named Voicebox profiles; MeloTTS stays the fallback."""
        if not (settings.voicebox_url and settings.voicebox_profiles):
            return
        if not settings.voicebox_attestation.strip():
            logger.warning(
                "voicebox_profiles ignored: voicebox_attestation (authorization to use "
                "these voices) is required"
            )
            return
        for character_id, name in settings.voicebox_profiles.items():
            try:
                character = get_character(character_id)
                base = self.voice.profiles.get(character_profile_id(character.id))
                if base is None or base.speaker_embedding or not str(name).strip():
                    continue
                self.voice.profiles.add_runtime(
                    replace(base, engine="voicebox", base_speaker=str(name)[:64])
                )
            except (ValueError, TypeError):
                logger.warning("Invalid voicebox profile mapping for %s", character_id)

    @property
    def character(self) -> Character:
        try:
            return get_character(self.memory.get("character") or DEFAULT_CHARACTER)
        except ValueError:
            return get_character(DEFAULT_CHARACTER)

    def character_voice(self) -> CharacterVoice:
        """Cached per character so phrase rotation persists across turns."""
        character = self.character
        voice = self._voices.setdefault(character.id, CharacterVoice(character))
        voice.sarcasm = self.memory.get("sarcasm") != "off"
        voice.swearing = self.memory.get("swearing") == "on"
        return voice

    def set_swearing(self, enabled: bool) -> None:
        self.memory.set("swearing", "on" if enabled else "off")

    def set_sarcasm(self, enabled: bool) -> None:
        self.memory.set("sarcasm", "on" if enabled else "off")

    def set_character(self, character_id: str) -> Character:
        character = get_character(character_id)
        self.memory.set("character", character.id)
        return character

    def enroll_character_voice(
        self,
        character_id: str,
        reference_wav: bytes,
        speaker: str,
        attestation: str,
        engine_id: str = "openvoice_v2",
    ) -> str:
        """Bind an authorized reference recording to a character.

        OpenVoice stores a tone-colour embedding; Chatterbox stores the validated WAV itself.
        """
        character = get_character(character_id)
        if engine_id not in ("openvoice_v2", "chatterbox"):
            raise ValueError("unsupported engine for enrollment")
        engine = self.voice.engines.get(engine_id)
        if engine is None or self._data_dir is None:
            raise ValueError(f"the {engine_id} engine is not configured")
        consent = new_consent(speaker, attestation)
        if not consent.speaker.strip() or not consent.attestation.strip():
            raise ValueError("the speaker and an authorization statement are required")
        embedding = engine.extract_embedding(reference_wav)
        suffix = "reference.wav" if engine_id == "chatterbox" else "speaker.pth"
        relative = Path(character.id) / suffix
        target = self._data_dir / "voice" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(embedding)
        target.chmod(0o600)
        base = self.voice.profiles.get(character_profile_id(character.id))
        self.voice.profiles.add(
            VoiceProfile(
                character_profile_id(character.id),
                engine=engine_id,
                language=character.language,
                speed=character.speed,
                speaker_embedding=str(relative),
                fallback_engine="melotts",
                consent=consent,
                base_speaker=base.base_speaker if base else character.base_speaker,
            )
        )
        return str(relative)

    def remove_character_voice(self, character_id: str) -> None:
        get_character(character_id)
        self.voice.profiles.reset_character(character_id)
        if self._data_dir is not None:
            for name in ("speaker.pth", "reference.wav"):
                (self._data_dir / "voice" / character_id / name).unlink(missing_ok=True)

    def say(self, kind: str, message: str) -> str:
        """Phrase a notification in the active character; the facts stay unchanged."""
        return self.character_voice().render(kind, message)

    def speak(self, text: str):
        return self.voice.speak(text, self.character.id)

    def _observed_logs(self) -> list[str]:
        """Evidence for the analyzer: in-process lines plus the journal, both observed by Kenpi."""
        try:
            self.log_store.add_records(read_journal(self._journal_units, 200))
        except (JournalUnavailable, ValueError) as error:
            logger.debug("Journal not ingested: %s", error)
        return self._log_ring.lines() + self.log_store.analyzer_lines()

    @staticmethod
    def _build_provider(settings: AssistantSettings) -> ChatCompletionsProvider | None:
        if not settings.llm_url:
            return None
        try:
            return ChatCompletionsProvider(
                settings.llm_url,
                settings.llm_model,
                settings.llm_timeout_seconds,
                settings.llm_allow_remote,
                settings.llm_api_key,
            )
        except ValueError as error:
            logger.warning("Model provider disabled: %s", error)
            return None

    def setup_steps(self) -> list[tuple[str, bool | None, str]]:
        """(id, done, instruction). None means it cannot be verified from here."""
        phones = self.companion.evidence()
        models = {m["id"]: m["status"] for m in self.catalog.describe()}
        engines = {e["id"]: e["available"] for e in self.voice.engines.describe()}
        connected = phones["connected_count"]
        return [
            (
                "pair_phone",
                None if connected is None else connected > 0,
                "Run kenpi-devices pair on the Pi, scan the QR code in the Kenpi app, then approve the device "
                "(grant the assistant.use scope so the app can talk to me).",
            ),
            (
                "speech_models",
                models.get("openvoice-v2-converter") == "installed",
                "Download the OpenVoice and MeloTTS files, then run kenpi-models pin and install for each.",
            ),
            (
                "speech_service",
                any(engines.get(i) for i in ("melotts", "openvoice_v2", "chatterbox")),
                "Start the local speech service (python -m kenpi.voice.pipeline_server) and set openvoice_url.",
            ),
            (
                "character",
                self.memory.get("character") is not None,
                'Pick a voice character: say "switch to the professor" or run kenpi-assist character <id>.',
            ),
        ]

    def phone_request(
        self, device_id: str, device_name: str, method: str, path: str, body: dict
    ) -> tuple[int, dict[str, Any]]:
        """Authenticated companion-app routes. The gateway has already checked the device scope."""
        try:
            if method == "GET" and path == "/api/v1/assistant/status":
                phones = self.companion.evidence()
                return 200, {
                    "character": self.character.id,
                    "phones_connected": phones["connected_count"],
                    "setup": [
                        {"id": i, "done": d, "instruction": t}
                        for i, d, t in self.setup_steps()
                    ],
                }
            if method == "GET" and path == "/api/v1/assistant/diagnostics":
                return 200, self.diagnostics.run().describe()
            if method == "GET" and path == "/api/v1/assistant/logs":
                return 200, {
                    "lines": [redact(line) for line in self._log_ring.lines()[-100:]]
                }
            if method == "POST" and path == "/api/v1/assistant/logs":
                kept = self.companion.report(device_id, device_name, body)
                self.log_store.ingest(
                    "android", self.companion.latest_lines(device_id), dedupe=True
                )
                return 200, {"accepted": kept}
            if method == "POST" and path == "/api/v1/assistant/ask":
                text = body.get("text")
                if not isinstance(text, str) or not text.strip():
                    return 400, {"error": "text_required"}
                response = self.orchestrator.handle(
                    text[:500], f"phone-{device_id}", approved=False
                )
                return 200, response.describe()
        except ValueError as error:
            return 400, {"error": str(error)[:100]}
        except Exception as error:
            logger.warning("Phone request failed: %s", type(error).__name__)
            return 503, {"error": "assistant_unavailable"}
        return 404, {"error": "not_found"}

    def _clear_cache(self) -> str:
        cache = (self._data_dir / "cache") if self._data_dir else None
        if cache is None or not cache.is_dir():
            return "no cache to clear"
        shutil.rmtree(cache)
        return "cache cleared"

    def _register_tools(
        self,
        host_health: Callable[[], dict[str, Any]],
        evidence: Callable[[], dict[str, dict[str, str]]],
        library: LocalMediaLibrary | None,
    ) -> None:
        def capability_gate(name: str) -> Callable[[], str | None]:
            def gate() -> str | None:
                item = evidence().get(name)
                if item is None or item["state"] != "available":
                    return f"{name.replace('_', ' ')} is not verified on this system"
                return None

            return gate

        def recording_status(_: dict[str, Any]) -> dict[str, Any]:
            return {
                "message": "I can't verify the camera's recording state. "
                "Kenpi has no validated camera protocol, and the camera records independently."
            }

        def storage_status(_: dict[str, Any]) -> dict[str, Any]:
            health = host_health()
            free, total = (
                health.get("storage_free_bytes"),
                health.get("storage_total_bytes"),
            )
            if free is None or not total:
                return {"message": "Gateway storage readings are unavailable."}
            return {
                "message": f"The gateway has {free / 1e9:.1f} GB free ({free / total:.0%}). Camera card storage is not verified."
            }

        def system_status(args: dict[str, Any]) -> dict[str, Any]:
            health = host_health()
            temperature = health.get("cpu_temperature_c")
            if args.get("topic") == "temperature":
                if temperature is None:
                    return {
                        "message": "The CPU temperature sensor is unavailable. Camera temperature is not verified."
                    }
                return {
                    "message": f"The gateway CPU is {temperature:.1f} C. Camera temperature is not verified."
                }
            report = self.diagnostics.run()
            return {"message": report.render_text()}

        def run_diagnostics(_: dict[str, Any]) -> dict[str, Any]:
            report = self.diagnostics.run()
            if self._events is not None:
                self._events.publish(
                    EventType.ASSISTANT_DIAGNOSTICS,
                    "assistant",
                    faults=sorted(report.fault_ids()),
                )
            top = next(
                (c for c in report.causes if c.fault.severity is not Status.UNKNOWN),
                None,
            )
            message = report.render_text()
            if top is not None:
                guide = self.diagnostics.guide(top.fault.id)
                if guide:
                    message += "\nSuggested: " + " ".join(
                        s["instruction"] for s in guide["steps"]
                    )
            return {
                "message": message,
                "report": report.describe(),
                "tone": "diagnostic_fault" if top is not None else "diagnostic_ok",
            }

        def list_recordings(args: dict[str, Any]) -> dict[str, Any]:
            if library is None:
                return {
                    "message": "No local media directory is configured, and camera file access is not verified."
                }
            files = library.list_media()
            note = (
                " Per-camera filtering is not available." if args.get("camera") else ""
            )
            return {
                "message": f"There are {len(files)} local recordings.{note}",
                "count": len(files),
            }

        def read_vehicle_data(args: dict[str, Any]) -> dict[str, Any]:
            quantity = args["quantity"].strip().lower().replace(" ", "_")
            pid = VEHICLE_QUANTITIES.get(quantity)
            loop, diagnose = self._loop, self._diagnose
            if pid is None:
                known = ", ".join(q.replace("_", " ") for q in VEHICLE_QUANTITIES)
                return {"message": f"I can read: {known}."}
            if loop is None or diagnose is None:
                raise RuntimeError("vehicle diagnostics are not available")
            future = asyncio.run_coroutine_threadsafe(diagnose(0x01, pid, 3.0), loop)
            deadline = time.monotonic() + 5.0
            try:
                while True:
                    if cancelled():
                        raise RuntimeError("cancelled")
                    try:
                        result = future.result(timeout=0.1)
                        break
                    except concurrent.futures.TimeoutError:
                        if time.monotonic() >= deadline:
                            raise
            except BaseException:
                future.cancel()
                raise
            decoded = result.get("decoded")
            if result.get("status") != "ok" or not isinstance(decoded, dict):
                return {
                    "message": f"The vehicle did not return {quantity.replace('_', ' ')} "
                    f"({result.get('status')}).",
                    "result": result,
                }
            value = decoded["value"]
            return {
                "message": f"{quantity.replace('_', ' ').capitalize()} is "
                f"{value:g} {decoded['unit']}, as reported by the vehicle controller.",
                "result": result,
            }

        def vehicle_gate() -> str | None:
            state = (self._vehicle() if self._vehicle else None) or {}
            if self._diagnose is None or self._loop is None:
                return "vehicle diagnostics are not configured"
            if state.get("link_state") != "connected":
                return "the vehicle controller is not connected"
            return None

        def vehicle_verify(result: dict[str, Any]) -> str | None:
            inner = result.get("result")
            if (
                inner is not None
                and inner.get("status") == "ok"
                and "decoded" not in inner
            ):
                return "the response could not be decoded"
            return None

        def show_camera_preview(_: dict[str, Any]) -> dict[str, Any]:
            return {"message": "Preview requested."}

        def stop_recording(_: dict[str, Any]) -> dict[str, Any]:
            raise RuntimeError("never executable")

        def set_preference(args: dict[str, Any]) -> dict[str, Any]:
            if args["key"] == "character":
                return set_character({"character": args["value"].replace(" ", "_")})
            self.memory.set(args["key"], args["value"])
            return {
                "message": f"Okay, {args['key'].replace('_', ' ')} is now {args['value']}."
            }

        def set_character(args: dict[str, Any]) -> dict[str, Any]:
            character = self.set_character(args["character"])
            greeting = CharacterVoice(character).line("greeting") or ""
            return {"message": f"{greeting} I am now {character.name}.".strip()}

        def list_characters(_: dict[str, Any]) -> dict[str, Any]:
            names = ", ".join(c.name for c in CHARACTERS)
            return {"message": f"Available characters: {names}."}

        def search_logs(args: dict[str, Any]) -> dict[str, Any]:
            self._observed_logs()
            hits = self.log_store.search(args["query"], limit=10)
            if not hits:
                return {"message": "No matching log records.", "records": []}
            return {
                "message": f"Found {len(hits)} matching record(s); latest: {hits[-1].message[:120]}",
                "records": [h.describe() for h in hits],
            }

        def setup_help(_: dict[str, Any]) -> dict[str, Any]:
            steps = self.setup_steps()
            pending = [t for _, done, t in steps if done is not True]
            done = sum(1 for _, d, _ in steps if d is True)
            if not pending:
                return {"message": "Setup is complete."}
            return {
                "message": f"{done} of {len(steps)} setup steps are verified. Next: {pending[0]}"
            }

        def clear_preferences(_: dict[str, Any]) -> dict[str, Any]:
            self.memory.clear()
            self.orchestrator.conversations.delete()
            return {"message": "I have forgotten your preferences."}

        def describe_capabilities(_: dict[str, Any]) -> dict[str, Any]:
            names = ", ".join(
                t["name"].replace("_", " ") for t in self.tools.describe()
            )
            return {"message": f"I can: {names}."}

        A = Permission.AUTOMATIC
        for tool in (
            Tool(
                "recording_status",
                "Report what is verifiably known about recording",
                A,
                recording_status,
            ),
            Tool("storage_status", "Report gateway storage", A, storage_status),
            Tool(
                "system_status",
                "Report gateway status or temperature",
                A,
                system_status,
                {"topic": str},
            ),
            Tool("run_diagnostics", "Run non-invasive diagnostics", A, run_diagnostics),
            Tool(
                "list_recordings",
                "Count local recordings",
                A,
                list_recordings,
                {"camera": str, "day": str},
            ),
            Tool(
                "read_vehicle_data",
                "Read a live engine value from the vehicle (read-only)",
                A,
                read_vehicle_data,
                {"quantity": str},
                gate=vehicle_gate,
                timeout=8.0,
                verify=vehicle_verify,
            ),
            Tool(
                "show_camera_preview",
                "Show the live camera preview",
                A,
                show_camera_preview,
                gate=capability_gate("live_preview"),
            ),
            Tool(
                "stop_recording",
                "Stop recording (never available)",
                Permission.NEVER,
                stop_recording,
            ),
            Tool(
                "set_preference",
                "Set an operator preference",
                A,
                set_preference,
                {"key": str, "value": str},
            ),
            Tool(
                "set_character",
                "Switch the assistant character; history and context are kept",
                A,
                set_character,
                {"character": str},
            ),
            Tool(
                "search_logs", "Search diagnostic logs", A, search_logs, {"query": str}
            ),
            Tool("setup_help", "Guide the next setup step", A, setup_help),
            Tool("list_characters", "List assistant characters", A, list_characters),
            Tool(
                "clear_preferences", "Forget stored preferences", A, clear_preferences
            ),
            Tool(
                "describe_capabilities",
                "List what the assistant can do",
                A,
                describe_capabilities,
            ),
        ):
            self.tools.register(tool)

    async def start(self) -> None:
        try:
            self._loop = asyncio.get_running_loop()
            logging.getLogger().addHandler(self._log_ring)
            self._running = True
        except Exception:
            logger.warning("Assistant failed to start; core services are unaffected")

    async def stop(self) -> None:
        try:
            self.orchestrator.cancel_running()
            logging.getLogger().removeHandler(self._log_ring)
        finally:
            self._running = False
            self._loop = None

    def route(
        self, path: str, query: dict[str, list[str]]
    ) -> tuple[int, dict[str, Any]] | None:
        """Read-only GET routes. Returns None for paths this subsystem does not own."""
        try:
            if path == "/api/v1/voice/status":
                return 200, self.voice.status()
            if path == "/api/v1/voice/engines":
                return 200, {"engines": self.voice.engines.describe()}
            if path == "/api/v1/voice/catalog":
                return 200, {"models": self.catalog.describe()}
            if path == "/api/v1/voice/characters":
                return 200, {
                    "active": self.character.id,
                    "default": DEFAULT_CHARACTER,
                    "characters": [c.describe() for c in CHARACTERS],
                    "note": "Accents require an enrolled, authorized reference recording.",
                }
            if path == "/api/v1/voice/profiles":
                return 200, self.voice.profiles.describe()
            if path == "/api/v1/intelligence/tools":
                return 200, {"tools": self.tools.describe()}
            if path == "/api/v1/intelligence/plan":
                text = query.get("text", [""])[0][:500]
                return 200, {
                    "plan": Planner(self.tools).plan(text).describe(),
                    "executed": False,
                }
            if path == "/api/v1/diagnostics/report":
                return 200, self.diagnostics.run().describe()
            if path == "/api/v1/diagnostics/history":
                return 200, {"history": self.diagnostics.history.recent()}
        except Exception as error:
            logger.warning("Assistant route failed: %s", type(error).__name__)
            return 503, {"error": "assistant_unavailable"}
        return None

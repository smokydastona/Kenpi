"""Voice service: profile-driven synthesis with automatic fallback. Failures never propagate."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from kenpi.voice.engines import EngineRegistry, EngineUnavailable, SynthesisRequest
from kenpi.voice.profiles import ProfileStore, character_profile_id

logger = logging.getLogger(__name__)


class Transcriber(Protocol):
    """Optional speech recognition backend (e.g. whisper.cpp). Not bundled."""

    def transcribe(self, audio: bytes) -> str: ...


@dataclass(frozen=True, slots=True)
class SpeechResult:
    audio: bytes | None
    engine: str | None
    fell_back: bool
    reason: str


class VoiceService:
    def __init__(
        self,
        engines: EngineRegistry,
        profiles: ProfileStore,
        profile_dir: Path | None = None,
        transcriber: Transcriber | None = None,
    ) -> None:
        self.engines = engines
        self.profiles = profiles
        self._profile_dir = profile_dir
        self._transcriber = transcriber

    def speak(self, text: str, character_id: str | None = None) -> SpeechResult:
        profile = (
            self.profiles.get(character_profile_id(character_id))
            if character_id
            else None
        ) or self.profiles.active
        chain = [profile.engine] + (
            [profile.fallback_engine]
            if profile.fallback_engine and profile.fallback_engine != profile.engine
            else []
        )
        reasons = []
        for index, engine_id in enumerate(chain):
            engine = self.engines.get(engine_id)
            if engine is None:
                reasons.append(f"{engine_id}: not registered")
                continue
            embedding = None
            if (
                profile.speaker_embedding
                and engine.supports_cloning
                and self._profile_dir is not None
            ):
                embedding = self._profile_dir / profile.speaker_embedding
            elif (
                profile.speaker_embedding and not engine.supports_cloning and index == 0
            ):
                reasons.append(f"{engine_id}: cannot use a custom voice")
                continue
            try:
                audio = engine.synthesize(
                    SynthesisRequest(
                        text,
                        profile.language,
                        profile.style,
                        profile.speed,
                        embedding,
                        profile.base_speaker,
                    )
                )
            except EngineUnavailable as error:
                reasons.append(f"{engine_id}: {error}")
                continue
            except Exception as error:
                logger.warning("Engine %s crashed: %s", engine_id, type(error).__name__)
                reasons.append(f"{engine_id}: {type(error).__name__}")
                continue
            return SpeechResult(audio, engine_id, index > 0, "; ".join(reasons))
        return SpeechResult(
            None, None, False, "; ".join(reasons) or "no engine available"
        )

    def transcribe(self, audio: bytes) -> str | None:
        if self._transcriber is None:
            return None
        try:
            return self._transcriber.transcribe(audio)
        except Exception as error:
            logger.warning("Transcription failed: %s", type(error).__name__)
            return None

    def status(self) -> dict[str, Any]:
        return {
            "engines": self.engines.describe(),
            "active_profile": self.profiles.active.id,
            "transcription": "configured" if self._transcriber else "not_configured",
            "microphone": "not_validated",
        }

"""Voice profiles with validation and mandatory consent for speaker cloning."""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
LANGUAGES = frozenset({"en", "es", "fr", "zh", "ja", "ko"})
STYLES = frozenset({"neutral", "friendly", "calm", "urgent"})


@dataclass(frozen=True, slots=True)
class Consent:
    speaker: str  # who the voice belongs to
    attestation: str  # operator statement that they are authorized to use it
    granted_at: float


@dataclass(frozen=True, slots=True)
class VoiceProfile:
    id: str
    engine: str = "melotts"
    language: str = "en"
    style: str = "neutral"
    speed: float = 1.0
    speaker_embedding: str | None = None  # relative to the profile directory
    fallback_engine: str | None = "melotts"
    offline_required: bool = True
    consent: Consent | None = None
    base_speaker: str | None = None

    def __post_init__(self) -> None:
        if not _ID.match(self.id) or not _ID.match(self.engine):
            raise ValueError("invalid profile or engine id")
        if self.language not in LANGUAGES or self.style not in STYLES:
            raise ValueError("unsupported language or style")
        if (
            isinstance(self.speed, bool)
            or not isinstance(self.speed, (int, float))
            or not 0.5 <= self.speed <= 2.0
        ):
            raise ValueError("speed must be between 0.5 and 2.0")
        if self.fallback_engine is not None and not _ID.match(self.fallback_engine):
            raise ValueError("invalid fallback engine id")
        if self.speaker_embedding is not None:
            path = Path(self.speaker_embedding)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("speaker embedding must be a relative path")
            if (
                self.consent is None
                or not self.consent.speaker.strip()
                or not self.consent.attestation.strip()
            ):
                raise ValueError("a cloned or custom voice requires recorded consent")

    def describe(self) -> dict[str, Any]:
        # Embedding paths and consent details stay private.
        return {
            "id": self.id,
            "engine": self.engine,
            "language": self.language,
            "style": self.style,
            "speed": self.speed,
            "fallback_engine": self.fallback_engine,
            "offline_required": self.offline_required,
            "has_custom_voice": self.speaker_embedding is not None,
            "base_speaker": self.base_speaker,
        }


DEFAULT_PROFILE = VoiceProfile("kenpi_default")


def character_profile_id(character_id: str) -> str:
    return f"char_{character_id}"


def builtin_character_profiles() -> dict[str, VoiceProfile]:
    """Base-speaker profiles. They need no consent because they use no custom embedding."""
    from kenpi.voice.characters import CHARACTERS

    return {
        character_profile_id(c.id): VoiceProfile(
            character_profile_id(c.id),
            engine="openvoice_v2",
            language=c.language,
            speed=c.speed,
            base_speaker=c.base_speaker,
            fallback_engine="melotts",
        )
        for c in CHARACTERS
    }


class ProfileStore:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._builtin = builtin_character_profiles()
        self._profiles: dict[str, VoiceProfile] = {
            DEFAULT_PROFILE.id: DEFAULT_PROFILE,
            **self._builtin,
        }
        self._active = DEFAULT_PROFILE.id
        self._runtime: set[str] = set()  # config-derived overrides, never persisted
        if path is not None:
            self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            for item in data.get("profiles", []):
                consent = Consent(**item["consent"]) if item.get("consent") else None
                profile = VoiceProfile(**{**item, "consent": consent})
                self._profiles[profile.id] = profile
            if data.get("active") in self._profiles:
                self._active = data["active"]
        except (OSError, ValueError, TypeError, KeyError):
            logger.warning("Voice profiles not loaded; using defaults")

    def _save(self) -> None:
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            custom = [
                asdict(p)
                for p in self._profiles.values()
                if p.id != DEFAULT_PROFILE.id
                and p.id not in self._runtime
                and self._builtin.get(p.id) != p
            ]
            tmp.write_text(
                json.dumps({"active": self._active, "profiles": custom}),
                encoding="utf-8",
            )
            tmp.chmod(0o600)
            tmp.replace(self._path)
        except OSError as error:
            logger.warning("Voice profiles not persisted: %s", type(error).__name__)

    def add(self, profile: VoiceProfile) -> None:
        if profile.id == DEFAULT_PROFILE.id:
            raise ValueError("the default profile cannot be replaced")
        self._profiles[profile.id] = profile
        self._save()

    def add_runtime(self, profile: VoiceProfile) -> None:
        """Override a profile from configuration for this run only (not written to disk)."""
        self._profiles[profile.id] = profile
        self._runtime.add(profile.id)

    def activate(self, profile_id: str) -> None:
        if profile_id not in self._profiles:
            raise KeyError(profile_id)
        self._active = profile_id
        self._save()

    def delete(self, profile_id: str) -> None:
        if profile_id == DEFAULT_PROFILE.id:
            raise ValueError("the default profile cannot be deleted")
        self._profiles.pop(profile_id)
        if self._active == profile_id:
            self._active = DEFAULT_PROFILE.id
        self._save()

    def get(self, profile_id: str) -> VoiceProfile | None:
        return self._profiles.get(profile_id)

    def reset_character(self, character_id: str) -> None:
        """Drop an enrolled voice and return to the built-in base-speaker profile."""
        pid = character_profile_id(character_id)
        if pid in self._builtin:
            self._profiles[pid] = self._builtin[pid]
            self._save()

    @property
    def active(self) -> VoiceProfile:
        return self._profiles[self._active]

    def describe(self) -> dict[str, Any]:
        return {
            "active": self._active,
            "profiles": [p.describe() for p in self._profiles.values()],
        }


def new_consent(speaker: str, attestation: str) -> Consent:
    return Consent(speaker, attestation, time.time())

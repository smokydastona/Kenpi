"""Replaceable speech engines. MeloTTS is the offline fallback; OpenVoice adds cloning. Both are MIT."""

from __future__ import annotations

import ipaddress
import json
import logging
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import ProxyHandler, Request, build_opener

logger = logging.getLogger(__name__)

MAX_TEXT = 1000


class EngineUnavailable(Exception):
    pass


@dataclass(frozen=True, slots=True)
class SynthesisRequest:
    text: str
    language: str = "en"
    style: str = "neutral"
    speed: float = 1.0
    speaker_embedding: Path | None = None
    base_speaker: str | None = None  # MeloTTS speaker key, e.g. EN-BR


class SpeechEngine(Protocol):
    id: str
    supports_cloning: bool

    def availability(self) -> tuple[bool, str]: ...

    def synthesize(self, request: SynthesisRequest) -> bytes: ...


class OpenVoiceEngine:
    """Client for an isolated OpenVoice inference process (separate PyTorch environment).

    Loopback hosts are accepted by default; a remote GPU host must be explicitly allowed.
    The service contract (GET /health, POST /synthesize -> audio/wav) is Kenpi's own.
    """

    id = "openvoice_v2"
    supports_cloning = True

    def __init__(
        self, url: str = "", allow_remote: bool = False, timeout: float = 20.0
    ) -> None:
        self._url = url.rstrip("/")
        self._timeout = timeout
        self._opener = build_opener(ProxyHandler({}))
        if url:
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                raise ValueError("OpenVoice URL must be http(s)")
            if not allow_remote and not _is_loopback(parsed.hostname):
                raise ValueError("remote OpenVoice hosts require allow_remote")

    def availability(self) -> tuple[bool, str]:
        if not self._url:
            return False, "not configured"
        try:
            with self._opener.open(
                f"{self._url}/health", timeout=min(self._timeout, 2.0)
            ) as response:
                return (
                    response.status == 200,
                    "ready" if response.status == 200 else "unhealthy",
                )
        except (URLError, OSError, ValueError):
            return False, "service unreachable"

    def synthesize(self, request: SynthesisRequest) -> bytes:
        if not self._url:
            raise EngineUnavailable("not configured")
        body: dict[str, Any] = {
            "text": request.text[:MAX_TEXT],
            "language": request.language,
            "style": request.style,
            "speed": request.speed,
            "base_speaker": request.base_speaker,
            "speaker_embedding": str(request.speaker_embedding)
            if request.speaker_embedding
            else None,
        }
        http_request = Request(
            f"{self._url}/synthesize",
            json.dumps(body).encode(),
            {"Content-Type": "application/json"},
        )
        try:
            with self._opener.open(http_request, timeout=self._timeout) as response:
                return response.read(32 * 1024 * 1024)
        except (URLError, OSError, ValueError) as error:
            raise EngineUnavailable(
                f"openvoice failed: {type(error).__name__}"
            ) from error

    def extract_embedding(self, reference_wav: bytes) -> bytes:
        """Ask the service for a tone-colour embedding of a reference recording."""
        if not self._url:
            raise EngineUnavailable("not configured")
        http_request = Request(
            f"{self._url}/extract",
            reference_wav,
            {"Content-Type": "audio/wav"},
        )
        try:
            with self._opener.open(http_request, timeout=self._timeout * 6) as response:
                return response.read(8 * 1024 * 1024)
        except (URLError, OSError, ValueError) as error:
            raise EngineUnavailable(
                f"openvoice failed: {type(error).__name__}"
            ) from error


class ChatterboxEngine(OpenVoiceEngine):
    """Client for the local Chatterbox service. The enrolled "embedding" is a reference WAV."""

    id = "chatterbox"
    supports_cloning = True


class MeloEngine(OpenVoiceEngine):
    """MeloTTS base speakers only (no cloning), served by the same local service."""

    id = "melotts"
    supports_cloning = False

    def synthesize(self, request: SynthesisRequest) -> bytes:
        return super().synthesize(replace(request, speaker_embedding=None))


def _is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


class EngineRegistry:
    def __init__(self) -> None:
        self._engines: dict[str, SpeechEngine] = {}

    def register(self, engine: SpeechEngine) -> None:
        if engine.id in self._engines:
            raise ValueError(f"engine already registered: {engine.id}")
        self._engines[engine.id] = engine

    def get(self, engine_id: str) -> SpeechEngine | None:
        return self._engines.get(engine_id)

    def describe(self) -> list[dict[str, Any]]:
        out = []
        for engine in self._engines.values():
            available, reason = engine.availability()
            out.append(
                {
                    "id": engine.id,
                    "available": available,
                    "detail": reason,
                    "supports_cloning": engine.supports_cloning,
                }
            )
        return out

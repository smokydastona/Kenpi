"""Replaceable speech engines. MeloTTS is the offline fallback; OpenVoice adds cloning. Both are MIT."""

from __future__ import annotations

import ipaddress
import json
import logging
import re
import time
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


_GENERATION_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class VoiceboxEngine:
    """Client for a user-run Voicebox (MIT, jamiepine/voicebox) REST API.

    Voicebox owns the voice profiles and models; Kenpi only sends text and the name or id of
    an existing Voicebox profile (carried in `SynthesisRequest.base_speaker`). It never sends
    reference audio, `personality` or `instruct`, so Kenpi's own character wording is what is
    spoken. Flow: POST /generate -> poll GET /generate/{id}/status (SSE) -> GET /audio/{id}.
    The API shapes come from Voicebox's source and are unvalidated against a running instance.
    """

    id = "voicebox"
    supports_cloning = False

    def __init__(
        self,
        url: str = "",
        allow_remote: bool = False,
        timeout: float = 60.0,
        default_profile: str = "",
    ) -> None:
        self._url = url.rstrip("/")
        self._timeout = timeout
        self._default_profile = default_profile
        self._opener = build_opener(ProxyHandler({}))
        if url:
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                raise ValueError("Voicebox URL must be http(s)")
            if not allow_remote and not _is_loopback(parsed.hostname):
                raise ValueError("remote Voicebox hosts require allow_remote")

    def availability(self) -> tuple[bool, str]:
        if not self._url:
            return False, "not configured"
        try:
            with self._opener.open(
                f"{self._url}/health", timeout=min(self._timeout, 2.0)
            ) as response:
                json.loads(response.read(65536))
                return True, "ready"
        except (URLError, OSError, ValueError):
            return False, "service unreachable"

    def _get_json(self, path: str, timeout: float) -> Any:
        with self._opener.open(f"{self._url}{path}", timeout=timeout) as response:
            return json.loads(response.read(4 * 1024 * 1024))

    def _resolve_profile(self, wanted: str) -> str:
        profiles = self._get_json("/profiles", min(self._timeout, 5.0))
        if isinstance(profiles, list):
            for profile in profiles:
                if not isinstance(profile, dict):
                    continue
                pid, name = profile.get("id"), profile.get("name")
                if wanted == pid or (
                    isinstance(name, str) and name.lower() == wanted.lower()
                ):
                    return str(pid)
        raise EngineUnavailable("voicebox profile not found")

    def _wait(self, generation_id: str, deadline: float) -> None:
        request = Request(f"{self._url}/generate/{generation_id}/status")
        with self._opener.open(request, timeout=min(self._timeout, 10.0)) as stream:
            while time.monotonic() < deadline:
                line = stream.readline(65536)
                if not line:
                    break
                if not line.startswith(b"data:"):
                    continue
                status = json.loads(line[5:]).get("status")
                if status == "completed":
                    return
                if status in ("failed", "not_found"):
                    raise EngineUnavailable(f"voicebox generation {status}")
        raise EngineUnavailable("voicebox generation timed out")

    def synthesize(self, request: SynthesisRequest) -> bytes:
        if not self._url:
            raise EngineUnavailable("not configured")
        wanted = request.base_speaker or self._default_profile
        if not wanted:
            raise EngineUnavailable("no voicebox profile selected")
        deadline = time.monotonic() + self._timeout
        generation_id = ""
        try:
            body = {
                "profile_id": self._resolve_profile(wanted),
                "text": request.text[:MAX_TEXT],
                "language": request.language,
            }
            started = Request(
                f"{self._url}/generate",
                json.dumps(body).encode(),
                {"Content-Type": "application/json"},
            )
            with self._opener.open(started, timeout=self._timeout) as response:
                generation_id = str(json.loads(response.read(1024 * 1024)).get("id"))
            if not _GENERATION_ID.match(generation_id):
                raise EngineUnavailable("voicebox returned an invalid id")
            self._wait(generation_id, deadline)
            with self._opener.open(
                f"{self._url}/audio/{generation_id}", timeout=self._timeout
            ) as response:
                return response.read(32 * 1024 * 1024)
        except EngineUnavailable:
            self._cancel(generation_id)
            raise
        except (URLError, OSError, ValueError) as error:
            self._cancel(generation_id)
            raise EngineUnavailable(
                f"voicebox failed: {type(error).__name__}"
            ) from error

    def _cancel(self, generation_id: str) -> None:
        if not _GENERATION_ID.match(generation_id):
            return
        try:
            self._opener.open(
                Request(
                    f"{self._url}/generate/{generation_id}/cancel", b"", method="POST"
                ),
                timeout=2.0,
            ).close()
        except (URLError, OSError, ValueError):
            pass


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

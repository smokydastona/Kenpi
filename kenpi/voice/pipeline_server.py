"""Loopback HTTP service for the OpenVoice V2 + MeloTTS pipeline.

Runs in its own process and virtualenv (PyTorch is far too heavy for the gateway process).
This module is standard library only; the heavy backend lives in
`kenpi.voice.openvoice_backend` and is imported only when the service starts.

Contract (matches `OpenVoiceEngine`):
  GET  /health      -> 200 {"status": "ok"} or 503 {"status": "<reason>"}
  POST /synthesize  -> JSON {text, language, speed, base_speaker, speaker_embedding} -> audio/wav
  POST /extract     -> audio/wav reference body -> application/octet-stream embedding
                       (Chatterbox: the validated reference WAV itself)

Embeddings are only read from beneath --voice-root, and only loopback binds are accepted.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import logging
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Protocol

logger = logging.getLogger("kenpi.openvoice")

MAX_TEXT = 1000
MAX_REFERENCE_BYTES = 32 * 1024 * 1024
_SPEAKER = re.compile(r"^[A-Za-z0-9-]{1,24}$")
_LANGUAGES = frozenset({"en", "es", "fr", "zh", "ja", "ko"})


class Backend(Protocol):
    def ready(self) -> tuple[bool, str]: ...

    def synthesize(
        self,
        text: str,
        language: str,
        speed: float,
        base_speaker: str | None,
        embedding: Path | None,
    ) -> bytes: ...

    def extract(self, wav: bytes) -> bytes: ...


class BadRequest(Exception):
    pass


def resolve_embedding(root: Path, value: object) -> Path | None:
    """Accept an embedding only if it is an existing file under the voice root."""
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise BadRequest("speaker_embedding must be a path")
    path = Path(value).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise BadRequest("speaker_embedding is outside the voice directory")
    return path


def validate_synthesis(body: object) -> tuple[str, str, float, str | None, object]:
    if not isinstance(body, dict):
        raise BadRequest("JSON object required")
    text = body.get("text")
    if not isinstance(text, str) or not text.strip():
        raise BadRequest("text is required")
    language = body.get("language", "en")
    if language not in _LANGUAGES:
        raise BadRequest("unsupported language")
    speed = body.get("speed", 1.0)
    if isinstance(speed, bool) or not isinstance(speed, (int, float)):
        raise BadRequest("speed must be a number")
    if not 0.5 <= speed <= 2.0:
        raise BadRequest("speed out of range")
    speaker = body.get("base_speaker")
    if speaker is not None and (
        not isinstance(speaker, str) or not _SPEAKER.match(speaker)
    ):
        raise BadRequest("invalid base_speaker")
    return (
        text[:MAX_TEXT],
        language,
        float(speed),
        speaker,
        body.get("speaker_embedding"),
    )


def make_handler(backend: Backend, voice_root: Path):
    # Inference is not re-entrant on the shared model objects.
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        server_version = "KenpiOpenVoice"

        def log_message(self, format: str, *args: object) -> None:
            logger.debug(format, *args)

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, payload: dict) -> None:
            self._send(status, json.dumps(payload).encode(), "application/json")

        def _read(self, limit: int) -> bytes:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                raise BadRequest("invalid Content-Length") from None
            if length <= 0 or length > limit:
                raise BadRequest("body missing or too large")
            return self.rfile.read(length)

        def do_GET(self) -> None:
            if self.path != "/health":
                return self._json(404, {"error": "not_found"})
            ready, detail = backend.ready()
            self._json(200 if ready else 503, {"status": "ok" if ready else detail})

        def do_POST(self) -> None:
            try:
                if self.path == "/synthesize":
                    raw = self._read(64 * 1024)
                    try:
                        parsed = json.loads(raw)
                    except ValueError:
                        raise BadRequest("invalid JSON") from None
                    text, language, speed, speaker, embedding = validate_synthesis(
                        parsed
                    )
                    path = resolve_embedding(voice_root, embedding)
                    with lock:
                        audio = backend.synthesize(text, language, speed, speaker, path)
                    return self._send(200, audio, "audio/wav")
                if self.path == "/extract":
                    wav = self._read(MAX_REFERENCE_BYTES)
                    with lock:
                        data = backend.extract(wav)
                    return self._send(200, data, "application/octet-stream")
                self._json(404, {"error": "not_found"})
            except BadRequest as error:
                self._json(400, {"error": str(error)})
            except Exception as error:
                logger.warning("Request failed: %s", type(error).__name__)
                self._json(500, {"error": "inference_failed"})

    return Handler


def serve(
    backend: Backend, voice_root: Path, host: str, port: int
) -> ThreadingHTTPServer:
    if not ipaddress.ip_address(host).is_loopback:
        raise ValueError("the OpenVoice service only binds to loopback addresses")
    return ThreadingHTTPServer((host, port), make_handler(backend, voice_root))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kenpi-openvoice-service")
    parser.add_argument(
        "--models", type=Path, required=True, help="assistant models directory"
    )
    parser.add_argument(
        "--voice-root", type=Path, required=True, help="assistant voice directory"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8780)
    parser.add_argument("--device", default="cpu")
    parser.add_argument(
        "--backend",
        choices=("openvoice", "chatterbox"),
        default="openvoice",
        help="openvoice serves OpenVoice V2 + MeloTTS (default port 8780); "
        "chatterbox serves Chatterbox (use --port 8781)",
    )
    parser.add_argument(
        "--variant", choices=("turbo", "v1", "multilingual"), default="turbo"
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)

    if args.backend == "chatterbox":
        from kenpi.voice.chatterbox_backend import ChatterboxBackend

        backend = ChatterboxBackend(args.device, args.variant)
    else:
        from kenpi.voice.openvoice_backend import OpenVoiceMeloBackend

        backend = OpenVoiceMeloBackend(args.models, args.device)
    server = serve(backend, args.voice_root, args.host, args.port)
    logger.info("OpenVoice service listening on %s:%d", args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

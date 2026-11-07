"""Chatterbox (MIT) expressive TTS with reference-audio voice prompting.

UNVALIDATED: written against the public Chatterbox Python API without the package, weights
or a GPU available. Imports are lazy. Full models are not assumed to run in real time on a
Raspberry Pi 5; benchmark Turbo there or point `chatterbox_url` at a GPU host.

Chatterbox conditions on a reference recording instead of an embedding, so an enrolled
character voice is stored as a WAV and passed here as `embedding` (a path).
"""

from __future__ import annotations

import io
import logging
import tempfile
import threading
import wave
from pathlib import Path

logger = logging.getLogger("kenpi.chatterbox")

MAX_REFERENCE_SECONDS = 60.0


def validate_reference(wav: bytes) -> bytes:
    """Accept only a decodable PCM WAV of sensible length; returns it unchanged."""
    try:
        with wave.open(io.BytesIO(wav)) as stream:
            frames, rate = stream.getnframes(), stream.getframerate()
    except (wave.Error, EOFError) as error:
        raise ValueError("reference must be a PCM WAV file") from error
    if rate <= 0 or not 3.0 <= frames / rate <= MAX_REFERENCE_SECONDS:
        raise ValueError("reference must be between 3 and 60 seconds long")
    return wav


class ChatterboxBackend:
    def __init__(self, device: str = "cpu", variant: str = "turbo") -> None:
        self._device = device
        self._variant = variant
        self._model = None
        self._lock = threading.Lock()

    def ready(self) -> tuple[bool, str]:
        try:
            import chatterbox  # noqa: F401
            import torchaudio  # noqa: F401
        except ImportError as error:
            return False, f"missing dependency: {error.name}"
        return True, "ready"

    def _load(self):
        with self._lock:
            if self._model is None:
                if self._variant == "multilingual":
                    from chatterbox.mtl_tts import ChatterboxMultilingualTTS as Model
                elif self._variant == "turbo":
                    from chatterbox.tts_turbo import ChatterboxTurboTTS as Model
                else:
                    from chatterbox.tts import ChatterboxTTS as Model
                self._model = Model.from_pretrained(device=self._device)
            return self._model

    def synthesize(self, text, language, speed, base_speaker, embedding) -> bytes:
        import torchaudio

        model = self._load()
        options: dict[str, object] = {}
        if embedding is not None:
            options["audio_prompt_path"] = str(embedding)
        if self._variant == "multilingual":
            options["language_id"] = language
        audio = model.generate(text, **options)
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "out.wav"
            torchaudio.save(str(out), audio, model.sr)
            return out.read_bytes()

    def extract(self, wav: bytes) -> bytes:
        return validate_reference(wav)

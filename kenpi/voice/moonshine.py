"""Optional Moonshine Voice (MIT, moonshine-ai/moonshine) speech-to-text and text-to-speech.

Both adapters import `moonshine_voice` lazily and run fully on-device. The SDK calls used
(`Transcriber(model_path=, model_arch=)`, `transcribe_without_streaming(audio, rate)`,
`TextToSpeech().language().voice().load()`, `synthesize(text) -> (samples, rate)`) come from
Moonshine's published docs and are unvalidated against the installed package or a Pi 5.
Only MIT-licensed models may be used: the legacy non-English non-streaming models are
non-commercial and must not be configured.
"""

from __future__ import annotations

import array
import threading
from pathlib import Path
from typing import Any, Callable

from kenpi.voice.audio import (
    MAX_UTTERANCE_SECONDS,
    AudioUnavailable,
    pcm_to_wav,
    wav_to_pcm,
)
from kenpi.voice.engines import EngineUnavailable, SynthesisRequest

MOONSHINE_LANGUAGES = {
    "en": "en-us",
    "es": "es-es",
    "fr": "fr-fr",
    "zh": "zh-hans",
    "ja": "ja-jp",
    "ko": "ko-kr",
}


def _import() -> Any:
    try:
        import moonshine_voice
    except ImportError as error:
        raise AudioUnavailable("moonshine-voice is not installed") from error
    return moonshine_voice


def _pcm16_to_floats(pcm: bytes) -> list[float]:
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) // 2 * 2])
    return [s / 32768.0 for s in samples]


def _floats_to_pcm16(samples: Any) -> bytes:
    out = array.array("h")
    for sample in samples:
        out.append(int(max(-1.0, min(1.0, float(sample))) * 32767))
    return out.tobytes()


class MoonshineTranscriber:
    """Satisfies voice.service.Transcriber (WAV bytes in, text out)."""

    def __init__(
        self,
        model_path: str = "",
        model_arch: str = "",
        factory: Callable[[], Any] | None = None,
    ) -> None:
        self._model_path = model_path
        self._model_arch = model_arch
        self._factory = factory
        self._transcriber: Any = None
        self._lock = threading.Lock()

    def available(self) -> bool:
        if self._factory is not None:
            return True
        if not self._model_path or not Path(self._model_path).is_dir():
            return False
        try:
            _import()
        except AudioUnavailable:
            return False
        return True

    def _load(self) -> Any:
        if self._transcriber is None:
            if self._factory is not None:
                self._transcriber = self._factory()
            else:
                module = _import()
                kwargs: dict[str, Any] = {"model_path": self._model_path}
                if self._model_arch:
                    kwargs["model_arch"] = self._model_arch
                self._transcriber = module.Transcriber(**kwargs)
        return self._transcriber

    def transcribe(self, audio: bytes) -> str:
        pcm, rate = wav_to_pcm(audio)
        if len(pcm) > MAX_UTTERANCE_SECONDS * rate * 2:
            raise ValueError("audio too long")
        if not self.available():
            raise AudioUnavailable("moonshine model or package is missing")
        samples = _pcm16_to_floats(pcm)
        with self._lock:
            try:
                transcript = self._load().transcribe_without_streaming(samples, rate)
            except AudioUnavailable:
                raise
            except Exception as error:
                raise AudioUnavailable(
                    f"moonshine failed: {type(error).__name__}"
                ) from error
        lines = getattr(transcript, "lines", None) or []
        return " ".join(
            " ".join(str(getattr(line, "text", "")).split()) for line in lines
        ).strip()


class MoonshineTTSEngine:
    """In-process offline speech engine (id `moonshine_tts`). No cloning; the Voicebox-style
    profile `base_speaker` optionally names a Moonshine catalog voice."""

    id = "moonshine_tts"
    supports_cloning = False

    def __init__(
        self,
        enabled: bool = False,
        factory: Callable[[str, str], Any] | None = None,
        max_chars: int = 1000,
    ) -> None:
        self._enabled = enabled
        self._factory = factory
        self._max_chars = max_chars
        self._engines: dict[tuple[str, str], Any] = {}
        self._lock = threading.Lock()  # a synthesizer speaks one reply at a time

    def availability(self) -> tuple[bool, str]:
        if not self._enabled:
            return False, "not enabled"
        if self._factory is not None:
            return True, "ready"
        try:
            _import()
        except AudioUnavailable:
            return False, "moonshine-voice not installed"
        return True, "ready"

    def _engine(self, language: str, voice: str) -> Any:
        key = (language, voice)
        if key not in self._engines:
            if self._factory is not None:
                self._engines[key] = self._factory(language, voice)
            else:
                tts = _import().TextToSpeech().language(language)
                if voice:
                    tts = tts.voice(voice)
                tts.load()
                self._engines[key] = tts
        return self._engines[key]

    def synthesize(self, request: SynthesisRequest) -> bytes:
        if not self._enabled:
            raise EngineUnavailable("not enabled")
        language = MOONSHINE_LANGUAGES.get(request.language)
        if language is None:
            raise EngineUnavailable("unsupported language")
        with self._lock:
            try:
                samples, rate = self._engine(
                    language, request.base_speaker or ""
                ).synthesize(request.text[: self._max_chars])
                return pcm_to_wav(_floats_to_pcm16(samples), int(rate))
            except (AudioUnavailable, EngineUnavailable):
                raise
            except Exception as error:
                raise EngineUnavailable(
                    f"moonshine tts failed: {type(error).__name__}"
                ) from error

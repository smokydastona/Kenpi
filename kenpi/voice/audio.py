"""Audio input: PCM/WAV helpers, energy-based voice activity detection, ALSA device discovery
and capture through `arecord`, and a whisper.cpp command-line transcriber.

Nothing here claims a microphone works: discovery and capture report what the OS tool says, and
every backend raises AudioUnavailable when its tool or device is missing.
"""

from __future__ import annotations

import io
import shutil
import struct
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Iterator, Sequence

SAMPLE_RATE = 16000
MAX_UTTERANCE_SECONDS = 30


class AudioUnavailable(RuntimeError):
    pass


def pcm_to_wav(pcm: bytes, rate: int = SAMPLE_RATE) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(pcm)
    return out.getvalue()


def wav_to_pcm(data: bytes) -> tuple[bytes, int]:
    """Validate a mono 16-bit WAV and return (pcm, rate)."""
    try:
        with wave.open(io.BytesIO(data), "rb") as wav:
            if wav.getnchannels() != 1 or wav.getsampwidth() != 2:
                raise ValueError("audio must be mono 16-bit PCM")
            return wav.readframes(wav.getnframes()), wav.getframerate()
    except wave.Error as error:
        raise ValueError(f"not a valid WAV file: {error}") from error


def rms(frame: bytes) -> float:
    count = len(frame) // 2
    if count == 0:
        return 0.0
    samples = struct.unpack(f"<{count}h", frame[: count * 2])
    return (sum(s * s for s in samples) / count) ** 0.5


@dataclass(slots=True)
class VadSettings:
    threshold: float = 500.0  # RMS on 16-bit samples
    frame_ms: int = 30
    start_frames: int = 3  # consecutive loud frames to open an utterance
    end_silence_ms: int = 600


def segment_utterances(
    frames: Iterable[bytes],
    settings: VadSettings | None = None,
    rate: int = SAMPLE_RATE,
) -> Iterator[bytes]:
    """Yield PCM utterances found in a stream of fixed-size frames."""
    cfg = settings or VadSettings()
    end_frames = max(1, cfg.end_silence_ms // cfg.frame_ms)
    max_frames = MAX_UTTERANCE_SECONDS * 1000 // cfg.frame_ms
    pre: list[bytes] = []
    current: list[bytes] = []
    loud = silent = 0
    for frame in frames:
        active = rms(frame) >= cfg.threshold
        if not current:
            pre.append(frame)
            del pre[: -cfg.start_frames]
            loud = loud + 1 if active else 0
            if loud >= cfg.start_frames:
                current, silent = list(pre), 0
                pre.clear()
            continue
        current.append(frame)
        silent = 0 if active else silent + 1
        if silent >= end_frames or len(current) >= max_frames:
            yield b"".join(current)
            current, loud = [], 0
    if current and silent < end_frames:
        yield b"".join(current)


def contains_wake_word(transcript: str, wake_words: Sequence[str]) -> bool:
    text = " ".join(transcript.lower().replace(",", " ").split())
    return any(w.lower() in text for w in wake_words if w.strip())


def strip_wake_word(transcript: str, wake_words: Sequence[str]) -> str:
    lowered = transcript.lower()
    for word in wake_words:
        index = lowered.find(word.lower())
        if word.strip() and index >= 0:
            return transcript[index + len(word) :].strip(" ,.!?")
    return transcript.strip()


@dataclass(frozen=True, slots=True)
class CaptureDevice:
    id: str
    description: str


def discover_devices(arecord: str = "arecord") -> list[CaptureDevice]:
    """List ALSA capture devices. Raises AudioUnavailable if arecord is missing or fails."""
    if shutil.which(arecord) is None:
        raise AudioUnavailable("arecord is not installed")
    try:
        done = subprocess.run(
            [arecord, "-l"], capture_output=True, text=True, timeout=5, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise AudioUnavailable(f"arecord failed: {type(error).__name__}") from error
    if done.returncode != 0:
        raise AudioUnavailable("arecord could not list devices")
    devices = []
    for line in done.stdout.splitlines():
        if line.startswith("card ") and "device" in line:
            try:
                card = line.split(":")[0].split()[1]
                device = line.split("device ")[1].split(":")[0]
            except IndexError:
                continue
            devices.append(CaptureDevice(f"plughw:{card},{device}", line.strip()))
    return devices


class AlsaCapture:
    """Reads fixed-size 16 kHz mono frames from `arecord` until stopped."""

    def __init__(
        self, device: str, frame_ms: int = 30, arecord: str = "arecord"
    ) -> None:
        self._argv = [
            arecord,
            "-q",
            "-D",
            device,
            "-f",
            "S16_LE",
            "-r",
            str(SAMPLE_RATE),
            "-c",
            "1",
            "-t",
            "raw",
        ]
        self._frame_bytes = SAMPLE_RATE * frame_ms // 1000 * 2

    def frames(self) -> Iterator[bytes]:
        try:
            proc = subprocess.Popen(
                self._argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
            )
        except OSError as error:
            raise AudioUnavailable(
                f"cannot start arecord: {type(error).__name__}"
            ) from error
        try:
            assert proc.stdout is not None
            while True:
                frame = proc.stdout.read(self._frame_bytes)
                if len(frame) < self._frame_bytes:
                    if proc.wait() != 0:
                        raise AudioUnavailable("capture device failed or disappeared")
                    return
                yield frame
        finally:
            proc.kill()
            proc.wait()


class WhisperCppTranscriber:
    """Runs a whisper.cpp CLI binary on a temporary WAV file. Satisfies voice.service.Transcriber."""

    def __init__(
        self, binary: str, model: Path, timeout: float = 60.0, language: str = "en"
    ) -> None:
        self._binary, self._model = binary, model
        self._timeout, self._language = timeout, language

    def available(self) -> bool:
        return shutil.which(self._binary) is not None and self._model.is_file()

    def transcribe(self, audio: bytes) -> str:
        pcm, rate = wav_to_pcm(audio)
        if rate != SAMPLE_RATE:
            raise ValueError("audio must be 16 kHz")
        if len(pcm) > MAX_UTTERANCE_SECONDS * SAMPLE_RATE * 2:
            raise ValueError("audio too long")
        if not self.available():
            raise AudioUnavailable("whisper.cpp binary or model is missing")
        with tempfile.TemporaryDirectory() as directory:
            wav_path = Path(directory) / "in.wav"
            wav_path.write_bytes(audio)
            try:
                done = subprocess.run(
                    [
                        self._binary,
                        "-m",
                        str(self._model),
                        "-f",
                        str(wav_path),
                        "-l",
                        self._language,
                        "-nt",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=self._timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired as error:
                raise AudioUnavailable("transcription timed out") from error
        if done.returncode != 0:
            raise AudioUnavailable(f"whisper.cpp exited with {done.returncode}")
        return " ".join(done.stdout.split())


def run_listener(
    frames: Iterable[bytes],
    transcribe: Callable[[bytes], str],
    handle: Callable[[str], None],
    wake_words: Sequence[str] = (),
    vad: VadSettings | None = None,
) -> int:
    """Segment utterances, transcribe each, require the wake word when configured, and pass the
    command text to `handle`. A failed transcription skips that utterance; returns handled count."""
    import logging

    handled = 0
    for pcm in segment_utterances(frames, vad):
        try:
            text = transcribe(pcm_to_wav(pcm))
        except (AudioUnavailable, ValueError) as error:
            logging.getLogger(__name__).warning("Utterance skipped: %s", error)
            continue
        if wake_words:
            if not contains_wake_word(text, wake_words):
                continue
            text = strip_wake_word(text, wake_words)
        if text:
            handle(text)
            handled += 1
    return handled

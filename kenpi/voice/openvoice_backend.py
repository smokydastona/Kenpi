"""OpenVoice V2 tone-colour converter on top of MeloTTS base speakers.

UNVALIDATED: written against the public OpenVoice V2 and MeloTTS APIs but never run on
target hardware (no PyTorch or checkpoints were available when it was written). Imports are
lazy so the module can be imported without the heavy dependencies.

Pipeline: MeloTTS renders the text in a base speaker (this supplies language and accent),
then, when a reference embedding is enrolled, the ToneColorConverter recolours the voice
to match it. Without an embedding the Melo output is returned unchanged.
"""

from __future__ import annotations

import io
import logging
import tempfile
import threading
from pathlib import Path

logger = logging.getLogger("kenpi.openvoice")

MELO_LANGUAGE = {"en": "EN", "es": "ES", "fr": "FR", "zh": "ZH", "ja": "JP", "ko": "KR"}
DEFAULT_SPEAKER = {
    "en": "EN-US",
    "es": "ES",
    "fr": "FR",
    "zh": "ZH",
    "ja": "JP",
    "ko": "KR",
}


class OpenVoiceMeloBackend:
    def __init__(self, models_dir: Path, device: str = "cpu") -> None:
        self._models = Path(models_dir)
        self._device = device
        self._converter = None
        self._melo: dict[str, object] = {}
        self._base_ses: dict[str, object] = {}
        self._load_lock = threading.Lock()

    def _converter_files(self) -> tuple[Path, Path]:
        base = self._models / "openvoice" / "converter"
        return base / "config.json", base / "checkpoint.pth"

    def ready(self) -> tuple[bool, str]:
        config, checkpoint = self._converter_files()
        if not (config.is_file() and checkpoint.is_file()):
            return False, "openvoice converter files not installed"
        try:
            import melo.api  # noqa: F401
            import openvoice.api  # noqa: F401
            import torch  # noqa: F401
        except ImportError as error:
            return False, f"missing dependency: {error.name}"
        return True, "ready"

    def _converter_model(self):
        with self._load_lock:
            if self._converter is None:
                from openvoice.api import ToneColorConverter

                config, checkpoint = self._converter_files()
                converter = ToneColorConverter(str(config), device=self._device)
                converter.load_ckpt(str(checkpoint))
                self._converter = converter
            return self._converter

    def _melo_model(self, language: str):
        code = MELO_LANGUAGE[language]
        with self._load_lock:
            if code not in self._melo:
                from melo.api import TTS

                folder = self._models / "melo" / code
                config, checkpoint = folder / "config.json", folder / "checkpoint.pth"
                if config.is_file() and checkpoint.is_file():
                    self._melo[code] = TTS(
                        language=code,
                        device=self._device,
                        config_path=str(config),
                        ckpt_path=str(checkpoint),
                    )
                else:
                    self._melo[code] = TTS(language=code, device=self._device)
            return self._melo[code]

    def _base_se(self, speaker_key: str):
        import torch

        name = speaker_key.lower().replace("_", "-")
        with self._load_lock:
            if name not in self._base_ses:
                path = (
                    self._models / "openvoice" / "base_speakers" / "ses" / f"{name}.pth"
                )
                if not path.is_file():
                    raise FileNotFoundError(f"base speaker embedding missing: {name}")
                self._base_ses[name] = torch.load(
                    path, map_location=self._device, weights_only=True
                )
            return self._base_ses[name]

    def synthesize(self, text, language, speed, base_speaker, embedding) -> bytes:
        import torch

        model = self._melo_model(language)
        speakers = model.hps.data.spk2id
        speaker_key = base_speaker or DEFAULT_SPEAKER[language]
        if speaker_key not in speakers:
            raise ValueError("base speaker not offered by this MeloTTS language")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "base.wav"
            model.tts_to_file(text, speakers[speaker_key], str(source), speed=speed)
            if embedding is None:
                return source.read_bytes()
            target_se = torch.load(
                embedding, map_location=self._device, weights_only=True
            )
            output = Path(directory) / "out.wav"
            self._converter_model().convert(
                audio_src_path=str(source),
                src_se=self._base_se(speaker_key),
                tgt_se=target_se,
                output_path=str(output),
                message="@Kenpi",
            )
            return output.read_bytes()

    def extract(self, wav: bytes) -> bytes:
        import torch
        from openvoice import se_extractor

        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / "reference.wav"
            reference.write_bytes(wav)
            embedding, _ = se_extractor.get_se(
                str(reference), self._converter_model(), vad=False
            )
            buffer = io.BytesIO()
            torch.save(embedding, buffer)
            return buffer.getvalue()

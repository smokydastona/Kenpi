"""Model catalog with licensing metadata and checksum-verified, operator-approved installs."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
FUNCTIONS = frozenset({"asr", "tts", "cloning", "vad", "wakeword"})


@dataclass(frozen=True, slots=True)
class ModelEntry:
    id: str
    version: str
    function: str
    hardware: str  # pi, cpu, gpu, external
    memory_mb: int
    languages: tuple[str, ...]
    offline: bool
    license: str
    license_verified: (
        bool  # whether the license of the model assets (not just code) was checked
    )
    source: str
    sha256: str  # empty until an operator pins a verified artifact
    file: str  # relative file name inside the models directory

    def __post_init__(self) -> None:
        if not _ID.match(self.id) or self.function not in FUNCTIONS:
            raise ValueError(f"invalid model entry: {self.id}")
        if self.sha256 and not re.fullmatch(r"[0-9a-f]{64}", self.sha256):
            raise ValueError("sha256 must be 64 lowercase hex characters")
        if self.file and (self.file.startswith("/") or ".." in Path(self.file).parts):
            raise ValueError("model file must be a relative path")

    def describe(self, status: str, pinned: bool | None = None) -> dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "function": self.function,
            "hardware": self.hardware,
            "memory_mb": self.memory_mb,
            "languages": list(self.languages),
            "offline": self.offline,
            "license": self.license,
            "license_verified": self.license_verified,
            "source": self.source,
            "pinned": bool(self.sha256) if pinned is None else pinned,
            "status": status,
            "requires_approval": True,
        }


def _entry(model_id, function, memory_mb, languages, license_, source, file, version):
    return ModelEntry(
        model_id,
        version,
        function,
        "external",
        memory_mb,
        languages,
        True,
        license_,
        False,
        source,
        "",
        file,
    )


def _openvoice_pipeline_entries() -> tuple[ModelEntry, ...]:
    """Files the OpenVoice V2 + MeloTTS service needs.

    Checksums are intentionally empty: they must be pinned from the artifacts the operator
    actually downloaded (`kenpi-models pin`), never copied from memory or documentation.
    """
    ov = "https://github.com/myshell-ai/OpenVoice"
    melo = "https://huggingface.co/myshell-ai"
    code = "MIT (code); weights terms to verify"
    return (
        _entry(
            "openvoice-v2-converter",
            "cloning",
            1200,
            ("multi",),
            code,
            ov,
            "openvoice/converter/checkpoint.pth",
            "v2",
        ),
        _entry(
            "openvoice-v2-converter-config",
            "cloning",
            1,
            ("multi",),
            code,
            ov,
            "openvoice/converter/config.json",
            "v2",
        ),
        _entry(
            "openvoice-v2-base-se-en-us",
            "cloning",
            1,
            ("en",),
            code,
            ov,
            "openvoice/base_speakers/ses/en-us.pth",
            "v2",
        ),
        _entry(
            "openvoice-v2-base-se-en-br",
            "cloning",
            1,
            ("en",),
            code,
            ov,
            "openvoice/base_speakers/ses/en-br.pth",
            "v2",
        ),
        _entry(
            "melotts-english",
            "tts",
            800,
            ("en",),
            code,
            f"{melo}/MeloTTS-English",
            "melo/EN/checkpoint.pth",
            "v1",
        ),
        _entry(
            "melotts-english-config",
            "tts",
            1,
            ("en",),
            code,
            f"{melo}/MeloTTS-English",
            "melo/EN/config.json",
            "v1",
        ),
        _entry(
            "melotts-french",
            "tts",
            800,
            ("fr",),
            code,
            f"{melo}/MeloTTS-French",
            "melo/FR/checkpoint.pth",
            "v1",
        ),
        _entry(
            "melotts-french-config",
            "tts",
            1,
            ("fr",),
            code,
            f"{melo}/MeloTTS-French",
            "melo/FR/config.json",
            "v1",
        ),
        _entry(
            "chatterbox-turbo",
            "tts",
            1500,
            ("en",),
            "MIT (code and weights)",
            "https://huggingface.co/ResembleAI/chatterbox-turbo",
            "chatterbox/turbo/model.safetensors",
            "turbo",
        ),
        _entry(
            "chatterbox-multilingual",
            "cloning",
            3000,
            ("multi",),
            "MIT (code and weights)",
            "https://huggingface.co/ResembleAI/chatterbox",
            "chatterbox/multilingual/model.safetensors",
            "v3",
        ),
    )


# Candidates only: none is claimed to work on the target hardware until benchmarked.
DEFAULT_ENTRIES = (
    ModelEntry(
        "whisper.cpp",
        "pinned-by-operator",
        "asr",
        "pi",
        500,
        ("multi",),
        True,
        "MIT (code); model terms to verify",
        False,
        "https://github.com/ggml-org/whisper.cpp",
        "",
        "whisper/model.bin",
    ),
    *_openvoice_pipeline_entries(),
)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


class ModelCatalog:
    def __init__(
        self,
        models_dir: Path | None = None,
        entries: tuple[ModelEntry, ...] = DEFAULT_ENTRIES,
    ) -> None:
        self._dir = models_dir
        self._entries = {e.id: e for e in entries}
        self._pins = self._load_pins()

    @classmethod
    def from_manifest(cls, manifest: Path, models_dir: Path | None) -> "ModelCatalog":
        data = json.loads(manifest.read_text(encoding="utf-8"))
        entries = tuple(
            ModelEntry(**{**item, "languages": tuple(item["languages"])})
            for item in data["models"]
        )
        return cls(models_dir, entries)

    @property
    def _pins_path(self) -> Path | None:
        return self._dir / "pins.json" if self._dir else None

    def _load_pins(self) -> dict[str, str]:
        if self._pins_path is None:
            return {}
        try:
            data = json.loads(self._pins_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {
            k: v
            for k, v in data.items()
            if k in self._entries
            and isinstance(v, str)
            and re.fullmatch(r"[0-9a-f]{64}", v)
        }

    def expected_sha256(self, entry: ModelEntry) -> str:
        """A checksum shipped in the entry wins; operator pins only fill empty ones."""
        return entry.sha256 or self._pins.get(entry.id, "")

    def entry(self, model_id: str) -> ModelEntry | None:
        return self._entries.get(model_id)

    def pin(self, model_id: str, source: Path, accept_license: bool) -> str:
        """Record the SHA-256 of an operator-downloaded artifact (trust on first use)."""
        entry = self._entries.get(model_id)
        if entry is None:
            raise ValueError("unknown model")
        if not accept_license:
            raise PermissionError("the operator must accept the model license terms")
        if self._dir is None or self._pins_path is None:
            raise ValueError("no models directory configured")
        if entry.sha256:
            raise ValueError("this entry already ships a checksum")
        digest = sha256_of(source)
        self._pins[model_id] = digest
        self._dir.mkdir(parents=True, exist_ok=True)
        tmp = self._pins_path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(self._pins, indent=2, sort_keys=True), encoding="utf-8"
        )
        tmp.replace(self._pins_path)
        return digest

    def status(self, entry: ModelEntry) -> str:
        if self._dir is None or not entry.file:
            return "available"
        path = self._dir / entry.file
        if not path.is_file():
            return "available"
        expected = self.expected_sha256(entry)
        if not expected:
            return "unverified"
        try:
            return "installed" if sha256_of(path) == expected else "failed"
        except OSError:
            return "failed"

    def describe(self) -> list[dict[str, Any]]:
        return [
            entry.describe(self.status(entry), bool(self.expected_sha256(entry)))
            for entry in self._entries.values()
        ]

    def install_from_file(self, model_id: str, source: Path, approved: bool) -> str:
        """Copy an operator-supplied file into the models directory after hash verification."""
        entry = self._entries.get(model_id)
        if entry is None:
            raise ValueError("unknown model")
        if not approved:
            raise PermissionError("operator approval required")
        if self._dir is None:
            raise ValueError("no models directory configured")
        expected = self.expected_sha256(entry)
        if not expected:
            raise ValueError("model has no pinned checksum; pin one before installing")
        if sha256_of(source) != expected:
            raise ValueError("checksum mismatch; file rejected")
        target = self._dir / entry.file
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_suffix(target.suffix + ".part")
        shutil.copyfile(source, partial)
        partial.replace(target)
        return "installed"

"""Local media library holding downloaded copies of camera recordings.

Camera originals are never touched: this module only reads and writes files in
its own directory. Downloads are written to a `.part` file and renamed into
place only after the provider returns successfully, so a crash or timeout can
never leave a truncated file that looks complete.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from kenpi.core.errors import KenpiError
from kenpi.media.service import CameraMediaProvider, MediaEntry

_VIDEO = {".mp4", ".mov", ".ts", ".avi", ".mkv"}
_PHOTO = {".jpg", ".jpeg", ".png"}
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,127}$")
_PART_SUFFIX = ".part"
LOCKED_MARKERS = ("event", "lock", "emr")


class MediaError(KenpiError):
    """A media operation was refused or failed."""


@dataclass(frozen=True, slots=True)
class LocalMedia:
    name: str
    size_bytes: int
    modified_epoch: float
    category: str

    def describe(self) -> dict[str, object]:
        return {
            "name": self.name,
            "size_bytes": self.size_bytes,
            "modified_epoch": self.modified_epoch,
            "category": self.category,
        }


def categorize(name: str) -> str:
    suffix = Path(name).suffix.lower()
    kind = "video" if suffix in _VIDEO else "photo" if suffix in _PHOTO else "other"
    if kind == "video" and any(marker in name.lower() for marker in LOCKED_MARKERS):
        return "event_video"
    return kind


def validate_name(name: str) -> str:
    if not isinstance(name, str) or not _SAFE_NAME.fullmatch(name) or ".." in name:
        raise MediaError("invalid media file name")
    if name.endswith(_PART_SUFFIX):
        raise MediaError("invalid media file name")
    return name


class LocalMediaLibrary:
    def __init__(self, root: str | Path, minimum_free_bytes: int = 512 * 1024 * 1024):
        if type(minimum_free_bytes) is not int or minimum_free_bytes < 0:
            raise ValueError("minimum_free_bytes must be a non-negative integer")
        self._root = Path(root)
        self._minimum_free = minimum_free_bytes
        self._transfer_lock = asyncio.Lock()

    @property
    def root(self) -> Path:
        return self._root

    def _resolve(self, name: str) -> Path:
        path = self._root / validate_name(name)
        if path.is_symlink():
            raise MediaError("symbolic links are not served")
        return path

    def recover_interrupted_transfers(self) -> tuple[str, ...]:
        """Remove partial downloads left by a crash; completed files are untouched."""
        removed: list[str] = []
        if not self._root.is_dir():
            return ()
        for leftover in sorted(self._root.glob(f"*{_PART_SUFFIX}")):
            if leftover.is_file() and not leftover.is_symlink():
                leftover.unlink()
                removed.append(leftover.name)
        return tuple(removed)

    def list_media(self) -> tuple[LocalMedia, ...]:
        if not self._root.is_dir():
            return ()
        items = []
        for entry in os.scandir(self._root):
            if (
                not entry.is_file(follow_symlinks=False)
                or entry.name.endswith(_PART_SUFFIX)
                or not _SAFE_NAME.fullmatch(entry.name)
            ):
                continue
            info = entry.stat(follow_symlinks=False)
            items.append(
                LocalMedia(
                    entry.name, info.st_size, info.st_mtime, categorize(entry.name)
                )
            )
        return tuple(sorted(items, key=lambda item: (-item.modified_epoch, item.name)))

    def open_path(self, name: str) -> Path:
        path = self._resolve(name)
        if not path.is_file():
            raise MediaError("media file not found")
        return path

    def delete_local_copy(self, name: str) -> None:
        """Delete only the local copy; the camera original is never addressed."""
        self.open_path(name).unlink()

    def storage(self) -> dict[str, int | bool]:
        self._root.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(self._root)
        return {
            "free_bytes": usage.free,
            "total_bytes": usage.total,
            "minimum_free_bytes": self._minimum_free,
            "has_space": usage.free > self._minimum_free,
        }

    async def download(
        self,
        provider: CameraMediaProvider,
        entry: MediaEntry,
        timeout_seconds: float,
    ) -> LocalMedia:
        name = validate_name(entry.name)
        async with self._transfer_lock:
            self._root.mkdir(parents=True, exist_ok=True)
            final = self._root / name
            if final.exists() or final.is_symlink():
                raise MediaError("a local file with this name already exists")
            free = shutil.disk_usage(self._root).free
            needed = (entry.size_bytes or 0) + self._minimum_free
            if free < needed:
                raise MediaError("insufficient local storage for this download")
            partial = self._root / (name + _PART_SUFFIX)
            try:
                await provider.download(entry.camera_id, str(partial), timeout_seconds)
                if not partial.is_file() or partial.stat().st_size == 0:
                    raise MediaError("download produced no data")
                if (
                    entry.size_bytes is not None
                    and partial.stat().st_size != entry.size_bytes
                ):
                    raise MediaError("downloaded size does not match camera listing")
                os.replace(partial, final)
            except BaseException:
                partial.unlink(missing_ok=True)
                raise
            info = final.stat()
            return LocalMedia(name, info.st_size, info.st_mtime, categorize(name))

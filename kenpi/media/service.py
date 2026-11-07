"""Media-service contracts; original camera files remain immutable."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class MediaEntry:
    camera_id: str
    name: str
    size_bytes: int | None = None
    duration_seconds: float | None = None
    captured_at: str | None = None


class CameraMediaProvider(Protocol):
    async def list_files(self, timeout_seconds: float) -> tuple[MediaEntry, ...]: ...

    async def download(
        self, camera_id: str, destination: str, timeout_seconds: float
    ) -> None: ...


class PlaybackService(Protocol):
    async def open(self, local_path: str) -> None: ...

    async def play(self) -> None: ...

    async def pause(self) -> None: ...

    async def seek(self, position_seconds: float) -> None: ...

    async def stop(self) -> None: ...

"""Video interfaces intentionally make no codec or transport assumptions."""

from dataclasses import dataclass
from typing import AsyncIterator, Protocol


@dataclass(frozen=True, slots=True)
class VideoFrame:
    data: bytes
    width: int
    height: int
    pts_seconds: float | None = None
    received_monotonic: float = 0.0

    def __post_init__(self) -> None:
        if not self.data or self.width <= 0 or self.height <= 0:
            raise ValueError("video frames require data and positive dimensions")


@dataclass(frozen=True, slots=True)
class VideoMetrics:
    frames_per_second: float | None = None
    latency_ms: float | None = None
    codec: str | None = None
    resolution: tuple[int, int] | None = None
    decoder_error: str | None = None


class LiveVideoPipeline(Protocol):
    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    def frames(self) -> AsyncIterator[VideoFrame]: ...

    async def metrics(self) -> VideoMetrics: ...

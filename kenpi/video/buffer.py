"""Frame buffering and measurement independent of any camera transport/codec."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Callable

from kenpi.video.pipeline import VideoFrame, VideoMetrics


class BoundedFrameBuffer:
    """Latest-frames buffer: when full the oldest frame is dropped, never blocking."""

    def __init__(self, capacity: int = 4) -> None:
        if type(capacity) is not int or not 1 <= capacity <= 64:
            raise ValueError("capacity must be between 1 and 64")
        self._frames: deque[VideoFrame] = deque()
        self._capacity = capacity
        self._ready = asyncio.Event()
        self._closed = False
        self.dropped = 0

    def put(self, frame: VideoFrame) -> None:
        if self._closed:
            raise RuntimeError("frame buffer is closed")
        if len(self._frames) >= self._capacity:
            self._frames.popleft()
            self.dropped += 1
        self._frames.append(frame)
        self._ready.set()

    def close(self) -> None:
        self._closed = True
        self._ready.set()

    async def get(self) -> VideoFrame | None:
        """Next frame, or None once closed and drained."""
        while True:
            if self._frames:
                frame = self._frames.popleft()
                if not self._frames and not self._closed:
                    self._ready.clear()
                return frame
            if self._closed:
                return None
            self._ready.clear()
            await self._ready.wait()


class MetricsTracker:
    """Sliding-window FPS and latency from frames actually delivered."""

    def __init__(
        self,
        window_seconds: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not 0.5 <= window_seconds <= 60:
            raise ValueError("window must be between 0.5 and 60 seconds")
        self._window = window_seconds
        self._clock = clock
        self._arrivals: deque[float] = deque()
        self._latency_ms: float | None = None
        self._resolution: tuple[int, int] | None = None
        self.codec: str | None = None
        self.decoder_error: str | None = None

    def record(self, frame: VideoFrame, decoded_at: float | None = None) -> None:
        now = self._clock() if decoded_at is None else decoded_at
        self._arrivals.append(now)
        self._resolution = (frame.width, frame.height)
        if frame.received_monotonic:
            self._latency_ms = max(0.0, (now - frame.received_monotonic) * 1000.0)
        self._trim(now)

    def _trim(self, now: float) -> None:
        while self._arrivals and now - self._arrivals[0] > self._window:
            self._arrivals.popleft()

    def metrics(self) -> VideoMetrics:
        now = self._clock()
        self._trim(now)
        fps = None
        if len(self._arrivals) >= 2:
            span = self._arrivals[-1] - self._arrivals[0]
            fps = (len(self._arrivals) - 1) / span if span > 0 else None
        return VideoMetrics(
            fps, self._latency_ms, self.codec, self._resolution, self.decoder_error
        )

    def is_stalled(self, stall_seconds: float) -> bool:
        """True when no frame arrived within `stall_seconds` (interruption detection)."""
        if not self._arrivals:
            return True
        return self._clock() - self._arrivals[-1] > stall_seconds

"""Host side of the vehicle link: message handling, link watchdog, serial I/O.

Nothing here controls dashcam power. A lost or silent controller only changes
the reported link state; it never triggers a shutdown. A shutdown request is
acknowledged only after the injected handler reports that it accepted it.
"""

from __future__ import annotations

import asyncio
import errno
import logging
import os
import termios
import time
from dataclasses import dataclass
from enum import Enum
from typing import Awaitable, Callable, Protocol

from kenpi.core.errors import ProtocolError
from kenpi.vehicle.messages import (
    Heartbeat,
    PowerState,
    PowerStateReport,
    ShutdownAck,
    ShutdownRequest,
    from_frame_message,
    to_frame_message,
)
from kenpi.vehicle.protocol import (
    SequenceTracker,
    VehicleMessageDecoder,
    encode_message,
)

logger = logging.getLogger(__name__)

ShutdownHandler = Callable[[int], Awaitable[bool]]


class LinkState(str, Enum):
    NO_DATA = "no_data"
    CONNECTED = "connected"
    LOST = "lost"


class ByteStream(Protocol):
    async def read(self) -> bytes: ...

    def write(self, data: bytes) -> None: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class VehicleSnapshot:
    link_state: LinkState
    power_state: PowerState | None
    ignition_on: bool | None
    controller_uptime_ms: int | None
    last_message_age_seconds: float | None
    shutdown_requested: bool
    shutdown_acknowledged: bool
    rejected_frames: int
    replayed_frames: int

    def describe(self) -> dict[str, object]:
        return {
            "link_state": self.link_state.value,
            "power_state": self.power_state.name.lower() if self.power_state else None,
            "ignition_on": self.ignition_on,
            "controller_uptime_ms": self.controller_uptime_ms,
            "last_message_age_seconds": self.last_message_age_seconds,
            "shutdown_requested": self.shutdown_requested,
            "shutdown_acknowledged": self.shutdown_acknowledged,
            "rejected_frames": self.rejected_frames,
            "replayed_frames": self.replayed_frames,
        }


class VehicleCoordinator:
    """Pure state tracking; time is supplied by an injected monotonic clock."""

    def __init__(
        self,
        heartbeat_timeout_seconds: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not 0.5 <= heartbeat_timeout_seconds <= 300:
            raise ValueError("heartbeat timeout must be between 0.5 and 300 seconds")
        self._timeout = heartbeat_timeout_seconds
        self._clock = clock
        self._decoder = VehicleMessageDecoder()
        self._sequence = SequenceTracker()
        self._last_message_at: float | None = None
        self._power_state: PowerState | None = None
        self._ignition_on: bool | None = None
        self._uptime_ms: int | None = None
        self._shutdown_grace: int | None = None
        self._shutdown_acknowledged = False
        self._rejected = 0
        self._replayed = 0

    def ingest(self, chunk: bytes) -> tuple[ShutdownRequest, ...]:
        """Process serial bytes; returns shutdown requests needing a decision."""
        requests: list[ShutdownRequest] = []
        for frame in self._decoder.feed(chunk):
            if not self._sequence.accept(frame.sequence):
                self._replayed += 1
                continue
            try:
                message = from_frame_message(frame)
            except ProtocolError:
                self._rejected += 1
                continue
            self._last_message_at = self._clock()
            if isinstance(message, Heartbeat):
                self._uptime_ms = message.uptime_ms
            elif isinstance(message, PowerStateReport):
                self._power_state = message.state
                self._ignition_on = message.ignition_on
            elif isinstance(message, ShutdownRequest):
                self._shutdown_grace = message.grace_seconds
                self._shutdown_acknowledged = False
                requests.append(message)
        return tuple(requests)

    def mark_acknowledged(self) -> None:
        self._shutdown_acknowledged = True

    def snapshot(self) -> VehicleSnapshot:
        age = (
            None
            if self._last_message_at is None
            else max(0.0, self._clock() - self._last_message_at)
        )
        if age is None:
            state = LinkState.NO_DATA
        else:
            state = LinkState.CONNECTED if age <= self._timeout else LinkState.LOST
        return VehicleSnapshot(
            state,
            self._power_state,
            self._ignition_on,
            self._uptime_ms,
            age,
            self._shutdown_grace is not None,
            self._shutdown_acknowledged,
            self._rejected,
            self._replayed,
        )


class VehicleLinkService:
    """Managed service reading the controller link and answering shutdown requests."""

    def __init__(
        self,
        coordinator: VehicleCoordinator,
        opener: Callable[[], ByteStream],
        shutdown_handler: ShutdownHandler | None = None,
    ) -> None:
        self._coordinator = coordinator
        self._opener = opener
        self._shutdown_handler = shutdown_handler
        self._stream: ByteStream | None = None
        self._task: asyncio.Task[None] | None = None
        self._tx_sequence = 0

    def snapshot(self) -> VehicleSnapshot:
        return self._coordinator.snapshot()

    async def start(self) -> None:
        if self._task is not None:
            raise RuntimeError("vehicle link is already running")
        self._stream = self._opener()
        self._task = asyncio.create_task(self._run(self._stream))

    async def stop(self) -> None:
        task, stream = self._task, self._stream
        self._task = self._stream = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if stream is not None:
            stream.close()

    async def _run(self, stream: ByteStream) -> None:
        while True:
            chunk = await stream.read()
            if not chunk:
                logger.warning("Vehicle link closed by peer")
                return
            for request in self._coordinator.ingest(chunk):
                await self._answer(stream, request)

    async def _answer(self, stream: ByteStream, request: ShutdownRequest) -> None:
        if self._shutdown_handler is None:
            logger.warning("Shutdown requested but no handler is configured")
            return
        try:
            accepted = await self._shutdown_handler(request.grace_seconds)
        except Exception:
            logger.exception("Shutdown handler failed; not acknowledging")
            return
        if accepted is not True:
            return
        self._tx_sequence = (self._tx_sequence + 1) & 0xFFFF
        stream.write(encode_message(to_frame_message(ShutdownAck(), self._tx_sequence)))
        self._coordinator.mark_acknowledged()


_BAUDS = {
    9600: termios.B9600,
    19200: termios.B19200,
    38400: termios.B38400,
    57600: termios.B57600,
    115200: termios.B115200,
    230400: termios.B230400,
}
SUPPORTED_BAUD_RATES = tuple(_BAUDS)


class SerialPort:
    """Raw 8N1 serial device read through the running event loop."""

    def __init__(self, path: str, baud: int) -> None:
        if baud not in _BAUDS:
            raise ValueError(f"unsupported baud rate {baud}")
        self._loop = asyncio.get_running_loop()
        self._fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            attrs = termios.tcgetattr(self._fd)
        except termios.error:
            os.close(self._fd)
            raise
        attrs[0] = 0
        attrs[1] = 0
        attrs[2] = _BAUDS[baud] | termios.CS8 | termios.CREAD | termios.CLOCAL
        attrs[3] = 0
        attrs[4] = attrs[5] = _BAUDS[baud]
        attrs[6][termios.VMIN] = 0
        attrs[6][termios.VTIME] = 0
        termios.tcsetattr(self._fd, termios.TCSANOW, attrs)
        self._waiter: asyncio.Future[None] | None = None
        self._closed = False

    async def read(self) -> bytes:
        while not self._closed:
            try:
                data = os.read(self._fd, 512)
            except BlockingIOError:
                data = None
            except OSError as exc:
                if exc.errno == errno.EIO:
                    return b""
                raise
            if data is not None and data:
                return data
            self._waiter = self._loop.create_future()
            self._loop.add_reader(self._fd, self._wake)
            try:
                await self._waiter
            finally:
                if not self._closed:
                    self._loop.remove_reader(self._fd)
        return b""

    def _wake(self) -> None:
        if self._waiter is not None and not self._waiter.done():
            self._waiter.set_result(None)

    def write(self, data: bytes) -> None:
        os.write(self._fd, data)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._loop.remove_reader(self._fd)
        except (ValueError, OSError):
            pass
        os.close(self._fd)

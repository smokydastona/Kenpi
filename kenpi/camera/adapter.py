"""Replaceable camera transport interfaces."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class ConnectionState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DEGRADED = "degraded"


@dataclass(frozen=True, slots=True)
class TransportResponse:
    payload: bytes
    received_at_monotonic: float


class CameraLinkAdapter(Protocol):
    """A transport plugin; concrete network details belong in verified adapters."""

    async def connect(self, timeout_seconds: float) -> None: ...

    async def close(self) -> None: ...

    async def request(
        self, payload: bytes, timeout_seconds: float
    ) -> TransportResponse: ...

    async def connection_state(self) -> ConnectionState: ...


class CameraProtocolCodec(Protocol):
    """Versioned protocol implementation supplied only after evidence exists."""

    @property
    def protocol_version(self) -> str: ...

    def encode_request(self, command: str, payload: bytes) -> bytes: ...

    def decode_response(self, payload: bytes) -> bytes: ...

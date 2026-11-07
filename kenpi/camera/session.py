"""Session lifecycle contract without guessed credentials or auth behavior."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol


class SessionState(str, Enum):
    ABSENT = "absent"
    ACTIVE = "active"
    EXPIRED = "expired"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class SessionSnapshot:
    state: SessionState
    expires_at: datetime | None = None
    evidence_note: str = "Authentication is not implemented pending A810 Lite evidence"


class CameraSessionProvider(Protocol):
    async def snapshot(self) -> SessionSnapshot: ...

    async def authenticate(self, timeout_seconds: float) -> SessionSnapshot: ...

    async def invalidate(self) -> None: ...

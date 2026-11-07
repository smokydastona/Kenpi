"""Shared typed models that avoid overstating device operation outcomes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, IntEnum
from typing import Generic, TypeVar


class EvidenceLevel(IntEnum):
    """Evidence strength for device-specific behavior."""

    E0 = 0
    E1 = 1
    E2 = 2
    E3 = 3
    E4 = 4
    E5 = 5


class CapabilityState(str, Enum):
    AVAILABLE = "available"
    NOT_VERIFIED = "not_verified"
    BLOCKED = "blocked"
    UNSUPPORTED = "unsupported"


class CommandSafety(str, Enum):
    READ_ONLY = "read_only"
    REVERSIBLE_WRITE = "reversible_write"
    STATE_CHANGING = "state_changing"
    UNKNOWN = "unknown"
    UNSUPPORTED = "unsupported"


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class OperationResult(Generic[T]):
    """An operation's observable milestones; success is never inferred."""

    request_created: bool
    transmitted: bool = False
    response_received: bool = False
    acknowledged: bool = False
    state_verified: bool | None = None
    value: T | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        if self.transmitted and not self.request_created:
            raise ValueError("A transmitted operation must have a created request")
        if self.response_received and not self.transmitted:
            raise ValueError("A response requires a transmitted request")
        if self.acknowledged and not self.response_received:
            raise ValueError("An acknowledgement requires a received response")
        if self.state_verified is True and not self.response_received:
            raise ValueError("Independent verification requires a received read-back")
        if self.error is not None and not self.error.strip():
            raise ValueError("Error details must be non-empty when supplied")

"""Evidence-bearing diagnostic data types. Missing evidence is UNKNOWN, never OK."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class Status(str, Enum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"
    UNKNOWN = "unknown"


class Subsystem(str, Enum):
    HOST = "host"
    CAMERA = "camera"
    VEHICLE = "vehicle"
    HEADUNIT = "headunit"
    NETWORK = "network"
    SERVICES = "services"
    VOICE = "voice"
    COMPANION = "companion"


@dataclass(frozen=True, slots=True)
class CheckResult:
    subsystem: Subsystem
    name: str
    status: Status
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def describe(self) -> dict[str, Any]:
        return {
            "subsystem": self.subsystem.value,
            "name": self.name,
            "status": self.status.value,
            "detail": self.detail,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True, slots=True)
class DiagnosticContext:
    """Read-only evidence sources. Any source may be absent."""

    host_health: Callable[[], dict[str, Any]] | None = None
    coordinator: Callable[[], dict[str, Any]] | None = None
    vehicle: Callable[[], dict[str, Any] | None] | None = None
    capabilities: Callable[[], dict[str, dict[str, str]]] | None = None
    voice_status: Callable[[], dict[str, Any]] | None = None
    companion: Callable[[], dict[str, Any]] | None = None
    system: Callable[[], dict[str, Any]] | None = None
    network: Callable[[], dict[str, Any]] | None = None
    log_lines: Callable[[], list[str]] | None = None
    clock: Callable[[], float] = time.time


@dataclass(frozen=True, slots=True)
class Fault:
    id: str
    subsystem: Subsystem
    severity: Status
    summary: str
    evidence: tuple[str, ...]

    def describe(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "subsystem": self.subsystem.value,
            "severity": self.severity.value,
            "summary": self.summary,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True, slots=True)
class Cause:
    fault: Fault
    confidence: float
    explanation: str

    def describe(self) -> dict[str, Any]:
        return {
            "fault": self.fault.describe(),
            "confidence": round(self.confidence, 2),
            "explanation": self.explanation,
        }

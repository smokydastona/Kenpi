"""Capability declarations remain unavailable until evidence is recorded."""

from dataclasses import dataclass

from kenpi.core.models import CapabilityState, EvidenceLevel


@dataclass(frozen=True, slots=True)
class Capability:
    name: str
    state: CapabilityState
    evidence: EvidenceLevel
    detail: str

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.detail.strip():
            raise ValueError("capability name and detail are required")
        if self.state is CapabilityState.AVAILABLE and self.evidence < EvidenceLevel.E4:
            raise ValueError("available hardware capabilities require at least E4 evidence")


INITIAL_CAPABILITIES = (
    Capability("camera_control", CapabilityState.BLOCKED, EvidenceLevel.E0, "No A810 Lite protocol evidence"),
    Capability("live_preview", CapabilityState.BLOCKED, EvidenceLevel.E0, "No A810 Lite stream evidence"),
    Capability("media_access", CapabilityState.BLOCKED, EvidenceLevel.E0, "No A810 Lite file access evidence"),
    Capability("kenwood_touchscreen", CapabilityState.BLOCKED, EvidenceLevel.E0, "Physical integration is unverified"),
    Capability("vehicle_power_coordination", CapabilityState.NOT_VERIFIED, EvidenceLevel.E0, "No ESP32 hardware or firmware validation"),
)

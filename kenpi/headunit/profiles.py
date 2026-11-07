"""Head-unit profile registry.

Profiles record what a head unit is *claimed* to offer and at what evidence
level. Nothing here is verified against hardware: every claim is E0/E1 until a
physical end-to-end test promotes it. Backup and front cameras are always owned
by the head unit; Kenpi is never placed on the reverse-camera path.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from kenpi.core.models import CapabilityState, EvidenceLevel


class VideoPath(str, Enum):
    HDMI = "hdmi"
    COMPOSITE_AV = "composite_av"
    WEBLINK = "weblink"
    PHONE_PROJECTION = "phone_projection"


class TouchPath(str, Enum):
    NONE = "none"
    USB_HID = "usb_hid"
    VENDOR_PROTOCOL = "vendor_protocol"


@dataclass(frozen=True, slots=True)
class HeadUnitProfile:
    key: str
    brand: str
    model: str
    claimed_video_paths: tuple[VideoPath, ...]
    touch_path: TouchPath
    native_rear_camera: bool
    native_second_camera: bool | None
    evidence: EvidenceLevel
    notes: str

    def __post_init__(self) -> None:
        if not self.key or self.key != self.key.lower() or " " in self.key:
            raise ValueError("profile key must be lowercase without spaces")
        if not self.brand.strip() or not self.model.strip() or not self.notes.strip():
            raise ValueError("brand, model, and notes are required")
        if self.evidence >= EvidenceLevel.E4:
            raise ValueError(
                "registry profiles are unverified; E4+ requires a hardware record"
            )

    @property
    def display_state(self) -> CapabilityState:
        return CapabilityState.NOT_VERIFIED

    def describe(self) -> dict[str, object]:
        return {
            "key": self.key,
            "brand": self.brand,
            "model": self.model,
            "claimed_video_paths": [path.value for path in self.claimed_video_paths],
            "touch_path": self.touch_path.value,
            "native_rear_camera": self.native_rear_camera,
            "native_second_camera": self.native_second_camera,
            "evidence": self.evidence.name,
            "state": self.display_state.value,
            "notes": self.notes,
        }


_SECONDARY = (
    " Claims come from secondary web sources that disagree on whether a generic "
    "HDMI source (such as a Raspberry Pi) is accepted; confirm with the "
    "manufacturer manual and bench test."
)

_PROFILES = (
    HeadUnitProfile(
        "kenwood-dmx5710s",
        "Kenwood",
        "DMX5710S",
        (),
        TouchPath.NONE,
        True,
        None,
        EvidenceLevel.E0,
        "Original target; see docs/kenwood-integration.md. No display path established.",
    ),
    HeadUnitProfile(
        "pioneer-dmh-w4660nex",
        "Pioneer",
        "DMH-W4660NEX",
        (VideoPath.HDMI, VideoPath.COMPOSITE_AV, VideoPath.WEBLINK),
        TouchPath.NONE,
        True,
        True,
        EvidenceLevel.E1,
        "Rear camera RCA with reverse trigger; AV input reportedly assignable "
        "as a 2nd camera." + _SECONDARY,
    ),
    HeadUnitProfile(
        "pioneer-dmh-wt7600nex",
        "Pioneer",
        "DMH-WT7600NEX",
        (VideoPath.HDMI,),
        TouchPath.NONE,
        True,
        None,
        EvidenceLevel.E1,
        "Reported micro-HDMI input; camera inputs unconfirmed." + _SECONDARY,
    ),
    HeadUnitProfile(
        "sony-xav-ax8500",
        "Sony",
        "XAV-AX8500",
        (VideoPath.HDMI,),
        TouchPath.NONE,
        True,
        None,
        EvidenceLevel.E1,
        "Reported HDMI input and a second camera input; unconfirmed." + _SECONDARY,
    ),
    HeadUnitProfile(
        "sony-xav-ax6000",
        "Sony",
        "XAV-AX6000",
        (VideoPath.HDMI,),
        TouchPath.NONE,
        True,
        False,
        EvidenceLevel.E1,
        "Reported single camera input." + _SECONDARY,
    ),
    HeadUnitProfile(
        "kenwood-dmx709s",
        "Kenwood",
        "DMX709S",
        (VideoPath.HDMI,),
        TouchPath.NONE,
        True,
        None,
        EvidenceLevel.E1,
        "Reported HDMI input; second camera unconfirmed." + _SECONDARY,
    ),
    HeadUnitProfile(
        "alpine-ilx-f511",
        "Alpine",
        "iLX-F511",
        (VideoPath.HDMI,),
        TouchPath.NONE,
        True,
        None,
        EvidenceLevel.E1,
        "Reported HDMI input; unconfirmed." + _SECONDARY,
    ),
    HeadUnitProfile(
        "generic-hdmi-display",
        "Generic",
        "Any HDMI display or head unit",
        (VideoPath.HDMI,),
        TouchPath.NONE,
        False,
        None,
        EvidenceLevel.E0,
        "Video-only fallback for any display with a standard HDMI input. "
        "No touch return; the UI must remain usable without it.",
    ),
)

PROFILES: dict[str, HeadUnitProfile] = {profile.key: profile for profile in _PROFILES}
DEFAULT_PROFILE_KEY = "kenwood-dmx5710s"


def get_profile(key: str) -> HeadUnitProfile:
    try:
        return PROFILES[key]
    except (KeyError, TypeError):
        raise ValueError(
            f"unknown head unit {key!r}; known: {', '.join(sorted(PROFILES))}"
        ) from None


def list_profiles() -> list[dict[str, object]]:
    return [profile.describe() for profile in PROFILES.values()]

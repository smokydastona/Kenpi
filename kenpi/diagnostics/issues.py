"""Known-issue database and step-by-step troubleshooting workflows."""

from __future__ import annotations

from dataclasses import dataclass

from kenpi.core.permissions import Permission


@dataclass(frozen=True, slots=True)
class Step:
    instruction: str
    action_id: str | None = None  # a registered remediation action, if any

    def describe(self) -> dict[str, object]:
        return {"instruction": self.instruction, "action_id": self.action_id}


@dataclass(frozen=True, slots=True)
class KnownIssue:
    fault_id: str
    title: str
    explanation: str
    steps: tuple[Step, ...]
    keywords: tuple[str, ...] = ()

    def describe(self) -> dict[str, object]:
        return {
            "fault_id": self.fault_id,
            "title": self.title,
            "explanation": self.explanation,
            "steps": [step.describe() for step in self.steps],
        }


_ISSUES = (
    KnownIssue(
        "host.memory_low",
        "Low memory",
        "Available RAM is very low. Heavy speech models are the usual cause on a Raspberry Pi.",
        (
            Step("Check which process is using the most memory."),
            Step(
                "If memory stays low, switch to the MeloTTS engine or move speech synthesis to an external inference host."
            ),
        ),
        ("slow", "memory", "ram"),
    ),
    KnownIssue(
        "host.storage_low",
        "Low storage",
        "The root filesystem is almost full, which can break logging and downloads.",
        (
            Step("Delete old local media copies you no longer need."),
            Step("Clear the assistant cache.", "clear_assistant_cache"),
            Step("Do not delete anything on the dashcam's own card from Kenpi."),
        ),
        ("storage", "disk", "full"),
    ),
    KnownIssue(
        "host.thermal",
        "High CPU temperature",
        "The CPU is hot and may throttle. Poor airflow or a heavy workload is likely.",
        (
            Step("Check enclosure ventilation and ambient temperature."),
            Step(
                "Disable the optional assistant until the temperature drops (kenpi-features disable assistant)."
            ),
        ),
        ("slow", "hot", "temperature", "throttle"),
    ),
    KnownIssue(
        "host.undervoltage",
        "Power supply undervoltage",
        "The Pi reported undervoltage. Wiring or the converter cannot supply enough current.",
        (Step("Check the 5 V supply, cable gauge and connectors under load."),),
        ("power", "voltage", "slow", "start"),
    ),
    KnownIssue(
        "host.storage_io",
        "Storage I/O errors",
        "The SD card or drive reported read/write errors, a sign of failing storage.",
        (Step("Back up with kenpi-backup, then replace or re-flash the storage."),),
        ("sd", "storage", "corrupt"),
    ),
    KnownIssue(
        "services.not_running",
        "Services not running",
        "Fewer services are running than configured.",
        (
            Step("Read the startup log: journalctl -u kenpi-status."),
            Step("Start in safe mode to isolate the failing service (kenpi --safe)."),
        ),
        ("start", "service", "crash"),
    ),
    KnownIssue(
        "services.restart_loop",
        "Service restart loop",
        "systemd stopped restarting a service because it failed repeatedly.",
        (
            Step(
                "Read the last error in the journal and fix its cause before restarting."
            ),
        ),
        ("start", "loop", "crash"),
    ),
    KnownIssue(
        "vehicle.heartbeat",
        "Controller link unhealthy",
        "The ESP32-S3 controller is silent or has never connected.",
        (
            Step("Check UART wiring, baud rate and controller power."),
            Step(
                "Confirm the controller firmware is running (docs/vehicle-power-management.md)."
            ),
        ),
        ("esp32", "controller", "ignition", "heartbeat"),
    ),
    KnownIssue(
        "vehicle.frames_rejected",
        "Controller frames rejected",
        "Frames failed authentication or framing checks; wiring noise or a key mismatch.",
        (
            Step(
                "Check ground and shielding, and that both sides share the same protocol version."
            ),
        ),
        ("esp32", "noise"),
    ),
    KnownIssue(
        "network.no_link",
        "No network link",
        "No non-loopback interface reports a link.",
        (Step("Check the cable, access point and the interface configuration."),),
        ("network", "wifi", "ethernet", "offline"),
    ),
    KnownIssue(
        "network.dns",
        "DNS failures",
        "Names cannot be resolved. Kenpi still works offline.",
        (Step("Check the DNS server and gateway settings."),),
        ("dns", "internet"),
    ),
    KnownIssue(
        "network.timeouts",
        "Timeouts",
        "Network operations are timing out.",
        (Step("Check link quality and the peer's availability."),),
        ("timeout", "network"),
    ),
    KnownIssue(
        "host.power_throttled",
        "Power or thermal throttling",
        "The firmware flagged under-voltage, frequency capping or throttling. This is a firmware report, not a guess.",
        (
            Step(
                "Use the official 5 V / 5 A supply and a short, thick cable; check the vehicle 12 V converter under load."
            ),
            Step("Improve cooling if the temperature check is also high."),
        ),
        ("power", "voltage", "throttl", "slow", "lightning"),
    ),
    KnownIssue(
        "host.cpu_overload",
        "CPU overloaded",
        "The one-minute load average is at least 1.5 per core.",
        (
            Step(
                "Check which service is busy (top / journalctl) and whether inference is running on the Pi."
            ),
        ),
        ("slow", "cpu", "load", "lag"),
    ),
    KnownIssue(
        "host.fs_readonly",
        "Filesystem is read-only",
        "A real filesystem is mounted read-only; Linux does this after I/O errors.",
        (
            Step(
                "Look for 'I/O error' or 'remounting filesystem read-only' in the kernel log (journalctl -k)."
            ),
            Step(
                "Suspect the SD card or SSD and its power supply; back up before any repair or reboot."
            ),
        ),
        ("read-only", "readonly", "sd card", "corrupt", "i/o error"),
    ),
    KnownIssue(
        "host.psu_limited",
        "Power supply below Raspberry Pi 5 rating",
        "The firmware reports the supply's advertised current. Below 5 A the Pi 5 limits USB power, "
        "which matters for a microphone, camera link and storage on the same board. "
        "This is the supply's advertisement, not a measurement of what the load draws.",
        (
            Step("Use a 5 V/5 A USB-C power supply that negotiates 5 A."),
            Step(
                "In a vehicle, check the converter's rating and wiring, not only the cable."
            ),
            Step(
                "Re-run diagnostics; also check for under-voltage in the throttling result."
            ),
        ),
        ("power supply", "psu", "5a", "usb power", "brownout"),
    ),
    KnownIssue(
        "host.usb_unbound",
        "USB device without a driver",
        "A USB device enumerated but the kernel bound no driver to at least one interface. "
        "This is observed, not diagnosed: the device may be unsupported, need firmware, or be failing.",
        (
            Step("Note the vendor:product id from the finding and look it up."),
            Step("Try another USB port and a short, known-good cable."),
            Step(
                "Check the kernel log (journalctl -k) around the time it was plugged in."
            ),
        ),
        ("usb", "dongle", "microphone", "audio device", "not detected"),
    ),
    KnownIssue(
        "network.cert_expiring",
        "Gateway certificate expiring",
        "The gateway certificate is expired or has under 30 days left. Re-issuing it changes the pinned fingerprint, so phones must re-pair.",
        (
            Step(
                "Plan a re-issue of the gateway certificate and re-pair the phone afterwards."
            ),
        ),
        ("certificate", "tls", "expired"),
    ),
    KnownIssue(
        "network.no_route",
        "No default route",
        "The routing table has no default route, so nothing outside the local link is reachable.",
        (Step("Check the uplink (Ethernet/Wi-Fi) and DHCP lease."),),
        ("route", "internet", "gateway"),
    ),
    KnownIssue(
        "companion.disconnected",
        "Phone cannot reach Kenpi",
        "A paired phone reported a connection problem and has no live session with the gateway.",
        (
            Step(
                "Make sure the phone is on the same network as the Pi, or that remote access is enabled for this device."
            ),
            Step(
                "Check that the gateway is running and the device has not been revoked (kenpi-devices list)."
            ),
            Step(
                "If the certificate changed, forget the gateway in the app and pair again."
            ),
        ),
        ("phone", "app", "companion", "connect", "pair"),
    ),
    KnownIssue(
        "voice.no_engine",
        "No speech engine",
        "Neither the primary nor fallback speech engine is installed.",
        (
            Step(
                "Start the local OpenVoice/MeloTTS service and install its pinned models (kenpi-models list)."
            ),
        ),
        ("voice", "speech", "tts"),
    ),
    KnownIssue(
        "voice.model_load",
        "Speech model failed to load",
        "A model file is missing, corrupt, or too large for the available memory.",
        (Step("Verify the model checksum against the catalog and check free memory."),),
        ("voice", "model"),
    ),
    KnownIssue(
        "voice.audio_device",
        "Audio device unavailable",
        "The microphone or speaker path could not be opened.",
        (
            Step(
                "Confirm the device exists (arecord -l) and the service may access it."
            ),
        ),
        ("voice", "microphone", "audio"),
    ),
    KnownIssue(
        "camera.live_preview_unverified",
        "Live preview unverified",
        "There is no verified stream implementation for the camera, so Kenpi cannot report preview state.",
        (Step("This is a known limitation: see docs/evidence-and-validation.md."),),
        ("camera", "preview", "video"),
    ),
    KnownIssue(
        "camera.camera_control_unverified",
        "Camera control unverified",
        "No camera protocol has been validated; Kenpi does not know the camera's recording state.",
        (Step("The camera records independently. Check its own indicator or app."),),
        ("camera", "recording"),
    ),
    KnownIssue(
        "camera.media_access_unverified",
        "Camera media access unverified",
        "File access for the camera is not validated.",
        (
            Step(
                "Copy files from the camera with its own tools into the local media directory."
            ),
        ),
        ("camera", "media", "files"),
    ),
)


class IssueDatabase:
    def __init__(self, issues: tuple[KnownIssue, ...] = _ISSUES) -> None:
        self._issues = {issue.fault_id: issue for issue in issues}

    def get(self, fault_id: str) -> KnownIssue | None:
        return self._issues.get(fault_id)

    def search(self, text: str) -> list[KnownIssue]:
        words = {w for w in text.lower().split() if len(w) > 2}
        scored = []
        for issue in self._issues.values():
            score = sum(1 for k in issue.keywords if k in words)
            if score:
                scored.append((score, issue))
        return [issue for _, issue in sorted(scored, key=lambda pair: -pair[0])]

    def all(self) -> list[KnownIssue]:
        return list(self._issues.values())


def required_permission_for_step(
    step: Step, actions: dict[str, Permission]
) -> Permission:
    if step.action_id is None:
        return Permission.AUTOMATIC
    return actions.get(step.action_id, Permission.NEVER)

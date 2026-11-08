"""Fault detection: deterministic rules over check results and log analysis."""

from __future__ import annotations

from kenpi.diagnostics.logs import LogAnalysis
from kenpi.diagnostics.models import CheckResult, Fault, Status, Subsystem


def _by_name(results: list[CheckResult]) -> dict[tuple[Subsystem, str], CheckResult]:
    return {(r.subsystem, r.name): r for r in results}


def detect_faults(results: list[CheckResult], logs: LogAnalysis | None) -> list[Fault]:
    index = _by_name(results)
    faults: list[Fault] = []

    def from_check(sub: Subsystem, name: str, fault_id: str, summary: str) -> None:
        result = index.get((sub, name))
        if result is not None and result.status in (Status.WARN, Status.FAIL):
            faults.append(
                Fault(fault_id, sub, result.status, summary, (result.detail,))
            )

    from_check(
        Subsystem.HOST, "memory", "host.memory_low", "The Raspberry Pi is low on memory"
    )
    from_check(
        Subsystem.HOST,
        "storage",
        "host.storage_low",
        "The Raspberry Pi is low on storage",
    )
    from_check(
        Subsystem.HOST,
        "cpu_temperature",
        "host.thermal",
        "The Raspberry Pi is running hot",
    )
    from_check(
        Subsystem.HOST,
        "throttling",
        "host.power_throttled",
        "The Raspberry Pi reports under-voltage or throttling",
    )
    from_check(
        Subsystem.HOST,
        "cpu_load",
        "host.cpu_overload",
        "The Raspberry Pi CPU is overloaded",
    )
    from_check(
        Subsystem.HOST,
        "filesystem",
        "host.fs_readonly",
        "A filesystem on the Pi is read-only",
    )
    from_check(
        Subsystem.HOST,
        "usb",
        "host.usb_unbound",
        "A USB device is connected but no driver is bound to it",
    )
    from_check(
        Subsystem.NETWORK,
        "tls_certificate",
        "network.cert_expiring",
        "The gateway TLS certificate is expired or expiring",
    )
    from_check(
        Subsystem.NETWORK, "routing", "network.no_route", "No default network route"
    )
    from_check(
        Subsystem.NETWORK, "interfaces", "network.no_link", "No network link is up"
    )
    from_check(
        Subsystem.VEHICLE,
        "controller_link",
        "vehicle.heartbeat",
        "The vehicle controller link is not healthy",
    )
    from_check(
        Subsystem.VEHICLE,
        "frame_integrity",
        "vehicle.frames_rejected",
        "Controller frames are being rejected",
    )
    from_check(
        Subsystem.SERVICES,
        "coordinator",
        "services.not_running",
        "Not all Kenpi services are running",
    )
    from_check(
        Subsystem.VOICE,
        "tts_engine",
        "voice.no_engine",
        "No speech engine is available",
    )

    from_check(
        Subsystem.COMPANION,
        "phone_link",
        "companion.disconnected",
        "A paired phone cannot reach Kenpi",
    )

    for name in ("camera_control", "live_preview", "media_access"):
        result = index.get((Subsystem.CAMERA, name))
        if result is not None and result.status is Status.UNKNOWN:
            faults.append(
                Fault(
                    f"camera.{name}_unverified",
                    Subsystem.CAMERA,
                    Status.UNKNOWN,
                    f"Camera {name.replace('_', ' ')} has no verified evidence",
                    (result.detail,),
                )
            )

    if logs is not None:
        log_rules = (
            (
                "restart_loop",
                "services.restart_loop",
                Subsystem.SERVICES,
                Status.FAIL,
                "A service is restarting repeatedly",
            ),
            (
                "undervoltage",
                "host.undervoltage",
                Subsystem.HOST,
                Status.FAIL,
                "The power supply reported undervoltage",
            ),
            (
                "out_of_memory",
                "host.out_of_memory",
                Subsystem.HOST,
                Status.FAIL,
                "A process ran out of memory",
            ),
            (
                "storage_io",
                "host.storage_io",
                Subsystem.HOST,
                Status.FAIL,
                "Storage reported I/O errors",
            ),
            (
                "dns_failure",
                "network.dns",
                Subsystem.NETWORK,
                Status.WARN,
                "Name resolution is failing",
            ),
            (
                "model_load_failed",
                "voice.model_load",
                Subsystem.VOICE,
                Status.WARN,
                "A speech model failed to load",
            ),
            (
                "audio_device",
                "voice.audio_device",
                Subsystem.VOICE,
                Status.WARN,
                "The audio device is unavailable",
            ),
            (
                "timeout",
                "network.timeouts",
                Subsystem.NETWORK,
                Status.WARN,
                "Operations are timing out",
            ),
        )
        for signature, fault_id, sub, severity, summary in log_rules:
            count = logs.signatures.get(signature, 0)
            if count:
                evidence = f"{count} log lines; last at {logs.last_seen.get(signature) or 'unknown time'}: {logs.sample[signature]}"
                faults.append(Fault(fault_id, sub, severity, summary, (evidence,)))
    return faults

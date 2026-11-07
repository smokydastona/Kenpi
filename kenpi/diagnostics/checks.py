"""Non-invasive health checks. They only read evidence and never change state."""

from __future__ import annotations

import logging
from typing import Callable

from kenpi.diagnostics.models import CheckResult, DiagnosticContext, Status, Subsystem

logger = logging.getLogger(__name__)

Check = Callable[[DiagnosticContext], list[CheckResult]]


class CheckRegistry:
    def __init__(self) -> None:
        self._checks: dict[str, Check] = {}

    def register(self, name: str, check: Check) -> None:
        if name in self._checks:
            raise ValueError(f"check already registered: {name}")
        self._checks[name] = check

    def names(self) -> list[str]:
        return list(self._checks)

    def run(self, context: DiagnosticContext) -> list[CheckResult]:
        """A failing check becomes UNKNOWN; it never aborts the others."""
        results: list[CheckResult] = []
        for name, check in self._checks.items():
            try:
                results.extend(check(context))
            except Exception as error:
                logger.warning(
                    "Diagnostic check %s failed: %s", name, type(error).__name__
                )
                results.append(
                    CheckResult(
                        Subsystem.SERVICES,
                        name,
                        Status.UNKNOWN,
                        "check could not run",
                        {"error": type(error).__name__},
                    )
                )
        return results


def _unknown(subsystem: Subsystem, name: str, why: str) -> CheckResult:
    return CheckResult(subsystem, name, Status.UNKNOWN, why)


def check_host(context: DiagnosticContext) -> list[CheckResult]:
    if context.host_health is None:
        return [_unknown(Subsystem.HOST, "host_health", "no host telemetry source")]
    health = context.host_health()
    results: list[CheckResult] = []
    total, available = (
        health.get("memory_total_bytes"),
        health.get("memory_available_bytes"),
    )
    if total and available is not None:
        ratio = available / total
        status = (
            Status.FAIL if ratio < 0.05 else Status.WARN if ratio < 0.10 else Status.OK
        )
        results.append(
            CheckResult(
                Subsystem.HOST,
                "memory",
                status,
                f"{ratio:.0%} of memory is available",
                {"available_ratio": round(ratio, 3)},
            )
        )
    else:
        results.append(_unknown(Subsystem.HOST, "memory", "memory reading unavailable"))
    free, size = health.get("storage_free_bytes"), health.get("storage_total_bytes")
    if size and free is not None:
        ratio = free / size
        status = (
            Status.FAIL if ratio < 0.03 else Status.WARN if ratio < 0.10 else Status.OK
        )
        results.append(
            CheckResult(
                Subsystem.HOST,
                "storage",
                status,
                f"{ratio:.0%} of storage is free",
                {"free_ratio": round(ratio, 3), "free_bytes": free},
            )
        )
    else:
        results.append(
            _unknown(Subsystem.HOST, "storage", "storage reading unavailable")
        )
    temperature = health.get("cpu_temperature_c")
    if temperature is None:
        results.append(
            _unknown(Subsystem.HOST, "cpu_temperature", "no temperature sensor reading")
        )
    else:
        status = (
            Status.FAIL
            if temperature >= 85
            else Status.WARN
            if temperature >= 80
            else Status.OK
        )
        results.append(
            CheckResult(
                Subsystem.HOST,
                "cpu_temperature",
                status,
                f"CPU temperature is {temperature:.1f} C",
                {"cpu_temperature_c": temperature},
            )
        )
    return results


def check_network(context: DiagnosticContext) -> list[CheckResult]:
    if context.host_health is None:
        return [_unknown(Subsystem.NETWORK, "interfaces", "no host telemetry source")]
    interfaces = (context.host_health().get("network") or {}).get("interfaces")
    if interfaces is None:
        return [_unknown(Subsystem.NETWORK, "interfaces", "interface list unavailable")]
    if not interfaces:
        return [
            CheckResult(
                Subsystem.NETWORK,
                "interfaces",
                Status.WARN,
                "no non-loopback interfaces found",
            )
        ]
    up = [item["name"] for item in interfaces if item.get("operstate") == "up"]
    status = Status.OK if up else Status.WARN
    detail = f"links up: {', '.join(up)}" if up else "no network link is up"
    return [CheckResult(Subsystem.NETWORK, "interfaces", status, detail, {"up": up})]


def check_network_routes(context: DiagnosticContext) -> list[CheckResult]:
    net = Subsystem.NETWORK
    if context.network is None:
        return [_unknown(net, "routing", "network telemetry not configured")]
    data = context.network()
    out: list[CheckResult] = []
    route = data.get("has_default_route")
    if route is None:
        out.append(_unknown(net, "routing", "routing table unreadable"))
    elif route:
        out.append(
            CheckResult(
                net,
                "routing",
                Status.OK,
                f"default route via {data.get('default_gateway')}",
            )
        )
    else:
        out.append(CheckResult(net, "routing", Status.WARN, "no default route"))
    dns = data.get("dns_servers")
    if dns is None:
        out.append(_unknown(net, "dns_config", "resolver configuration unreadable"))
    else:
        out.append(
            CheckResult(
                net,
                "dns_config",
                Status.OK if dns else Status.WARN,
                f"{len(dns)} resolver(s) configured"
                if dns
                else "no resolver configured",
            )
        )
    wg = data.get("wireguard_interfaces")
    out.append(
        _unknown(net, "wireguard", "interfaces unreadable")
        if wg is None
        else CheckResult(
            net,
            "wireguard",
            Status.OK if wg else Status.UNKNOWN,
            f"WireGuard interface(s): {', '.join(wg)}"
            if wg
            else "no WireGuard interface present",
        )
    )
    days = data.get("cert_days_left")
    if days is None:
        out.append(_unknown(net, "tls_certificate", "gateway certificate unreadable"))
    else:
        status = Status.FAIL if days <= 0 else Status.WARN if days < 30 else Status.OK
        out.append(
            CheckResult(
                net,
                "tls_certificate",
                status,
                f"gateway certificate valid for {days:.0f} more day(s)",
            )
        )
    return out


def check_vehicle(context: DiagnosticContext) -> list[CheckResult]:
    if context.vehicle is None:
        return [
            _unknown(
                Subsystem.VEHICLE, "controller_link", "vehicle link is not enabled"
            )
        ]
    link = context.vehicle()
    if link is None:
        return [
            _unknown(
                Subsystem.VEHICLE, "controller_link", "vehicle link is not enabled"
            )
        ]
    state = link.get("link_state")
    evidence = {
        "link_state": state,
        "last_message_age_seconds": link.get("last_message_age_seconds"),
        "rejected_frames": link.get("rejected_frames"),
    }
    if state == "connected":
        results = [
            CheckResult(
                Subsystem.VEHICLE,
                "controller_link",
                Status.OK,
                "controller heartbeat is current",
                evidence,
            )
        ]
    elif state == "lost":
        results = [
            CheckResult(
                Subsystem.VEHICLE,
                "controller_link",
                Status.FAIL,
                "controller heartbeat was lost",
                evidence,
            )
        ]
    else:
        results = [
            CheckResult(
                Subsystem.VEHICLE,
                "controller_link",
                Status.WARN,
                "no controller data received yet",
                evidence,
            )
        ]
    if link.get("rejected_frames"):
        results.append(
            CheckResult(
                Subsystem.VEHICLE,
                "frame_integrity",
                Status.WARN,
                f"{link['rejected_frames']} controller frames were rejected",
                {"rejected_frames": link["rejected_frames"]},
            )
        )
    return results


def check_capabilities(context: DiagnosticContext) -> list[CheckResult]:
    """Camera and head-unit state comes only from recorded evidence; never inferred."""
    if context.capabilities is None:
        return [
            _unknown(
                Subsystem.CAMERA, "camera_control", "capability evidence unavailable"
            )
        ]
    caps = context.capabilities()
    results = []
    mapping = {
        "camera_control": Subsystem.CAMERA,
        "live_preview": Subsystem.CAMERA,
        "media_access": Subsystem.CAMERA,
        "kenwood_touchscreen": Subsystem.HEADUNIT,
    }
    for name, subsystem in mapping.items():
        item = caps.get(name)
        if item is None:
            results.append(_unknown(subsystem, name, "capability not declared"))
        elif item["state"] == "available":
            results.append(
                CheckResult(subsystem, name, Status.OK, item["detail"], dict(item))
            )
        else:
            results.append(
                CheckResult(
                    subsystem,
                    name,
                    Status.UNKNOWN,
                    f"not verified ({item['state']}, {item['evidence']}): {item['detail']}",
                    dict(item),
                )
            )
    return results


def check_services(context: DiagnosticContext) -> list[CheckResult]:
    if context.coordinator is None:
        return [_unknown(Subsystem.SERVICES, "coordinator", "no coordinator status")]
    status = context.coordinator()
    configured = status.get("configured_service_count", 0)
    started = status.get("started_service_count", 0)
    evidence = {"configured": configured, "started": started}
    if configured and started == configured:
        return [
            CheckResult(
                Subsystem.SERVICES,
                "coordinator",
                Status.OK,
                f"{started} services running",
                evidence,
            )
        ]
    return [
        CheckResult(
            Subsystem.SERVICES,
            "coordinator",
            Status.FAIL,
            f"{started} of {configured} services running",
            evidence,
        )
    ]


def check_voice(context: DiagnosticContext) -> list[CheckResult]:
    if context.voice_status is None:
        return [
            _unknown(Subsystem.VOICE, "tts_engine", "voice subsystem not configured")
        ]
    voice = context.voice_status()
    available = [e["id"] for e in voice.get("engines", []) if e.get("available")]
    results = [
        CheckResult(
            Subsystem.VOICE,
            "tts_engine",
            Status.OK if available else Status.WARN,
            f"available engines: {', '.join(available)}"
            if available
            else "no speech engine is available",
            {"available": available},
        )
    ]
    results.append(
        CheckResult(
            Subsystem.VOICE,
            "microphone",
            Status.UNKNOWN,
            "microphone path is not validated on this hardware",
        )
    )
    return results


def check_system(context: DiagnosticContext) -> list[CheckResult]:
    from kenpi.diagnostics.system import THROTTLE_BITS

    if context.system is None:
        return [_unknown(Subsystem.HOST, "system", "system telemetry not configured")]
    data = context.system()
    results: list[CheckResult] = []
    load, cpus = data.get("load_1m"), data.get("cpu_count")
    if load is None or not cpus:
        results.append(_unknown(Subsystem.HOST, "cpu_load", "load average unavailable"))
    else:
        per_core = load / cpus
        status = Status.WARN if per_core >= 1.5 else Status.OK
        results.append(
            CheckResult(
                Subsystem.HOST,
                "cpu_load",
                status,
                f"1-minute load is {load:.2f} on {cpus} cores",
                {"per_core": round(per_core, 2)},
            )
        )
    total, used = data.get("swap_total_bytes"), data.get("swap_used_bytes")
    if total is None or used is None:
        results.append(_unknown(Subsystem.HOST, "swap", "swap reading unavailable"))
    elif total == 0:
        results.append(
            CheckResult(Subsystem.HOST, "swap", Status.OK, "no swap configured")
        )
    else:
        ratio = used / total
        results.append(
            CheckResult(
                Subsystem.HOST,
                "swap",
                Status.WARN if ratio > 0.5 else Status.OK,
                f"{ratio:.0%} of swap is in use",
                {"used_ratio": round(ratio, 3)},
            )
        )
    flags = data.get("throttled_flags")
    if flags is None:
        results.append(
            _unknown(
                Subsystem.HOST, "throttling", "firmware throttle flags unavailable"
            )
        )
    else:
        active = [t for b, t in THROTTLE_BITS.items() if flags & (1 << b)]
        now = flags & 0xF
        status = (
            Status.FAIL if flags & 1 else Status.WARN if (now or flags) else Status.OK
        )
        results.append(
            CheckResult(
                Subsystem.HOST,
                "throttling",
                status,
                "; ".join(active)
                if active
                else "no throttling or under-voltage recorded",
                {"flags": hex(flags)},
            )
        )
    usb = data.get("usb_devices")
    results.append(
        _unknown(Subsystem.HOST, "usb", "USB bus not readable")
        if usb is None
        else CheckResult(
            Subsystem.HOST,
            "usb",
            Status.OK,
            f"{len(usb)} USB device(s) enumerated",
            {"devices": usb},
        )
    )
    return results


def check_companion(context: DiagnosticContext) -> list[CheckResult]:
    if context.companion is None:
        return [
            _unknown(
                Subsystem.COMPANION, "phone_link", "companion tracking not configured"
            )
        ]
    evidence = context.companion()
    if not evidence.get("presence_known"):
        return [
            _unknown(
                Subsystem.COMPANION,
                "phone_link",
                "the gateway is not running, so phone connections cannot be observed",
            )
        ]
    count = evidence.get("connected_count") or 0
    if count:
        return [
            CheckResult(
                Subsystem.COMPANION,
                "phone_link",
                Status.OK,
                f"{count} companion phone(s) connected",
                {"connected": count},
            )
        ]
    troubled = [r for r in evidence.get("reports", []) if not r["connected_now"]]
    if troubled:
        last = troubled[0]
        return [
            CheckResult(
                Subsystem.COMPANION,
                "phone_link",
                Status.WARN,
                f'{last["name"]} reported "{last["phone_connection"]}" and is not connected',
                {
                    "phone_lines": last["problem_lines"],
                    "age_seconds": last["age_seconds"],
                },
            )
        ]
    return [
        _unknown(
            Subsystem.COMPANION, "phone_link", "no companion phone has connected yet"
        )
    ]


def default_registry() -> CheckRegistry:
    registry = CheckRegistry()
    for name, check in (
        ("host", check_host),
        ("network", check_network),
        ("vehicle", check_vehicle),
        ("capabilities", check_capabilities),
        ("services", check_services),
        ("voice", check_voice),
        ("companion", check_companion),
        ("system", check_system),
        ("network_routes", check_network_routes),
    ):
        registry.register(name, check)
    return registry

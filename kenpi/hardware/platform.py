"""Observe device-tree and kernel telemetry without assuming board wiring."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any


def collection_error_reason(error: OSError | UnicodeError) -> str:
    """Classify failures without disclosing paths or exception messages."""
    if isinstance(error, FileNotFoundError):
        return "not_found"
    if isinstance(error, PermissionError):
        return "permission_denied"
    if isinstance(error, UnicodeError):
        return "invalid_encoding"
    return "read_failed"


def _text(path: Path, metric: str, errors: list[dict[str, str]]) -> str | None:
    try:
        value = path.read_text(encoding="ascii").strip().rstrip("\0")
    except (OSError, UnicodeError) as error:
        errors.append({"metric": metric, "reason": collection_error_reason(error)})
        return None
    if not value:
        errors.append({"metric": metric, "reason": "missing_value"})
        return None
    return value


def read_temperature(
    path: Path, *, collection_errors: list[dict[str, str]]
) -> float | None:
    """Read the Linux thermal ABI's millidegrees Celsius."""
    value = _text(path, "cpu_temperature", collection_errors)
    if value is None:
        return None
    try:
        temperature = int(value) / 1000
    except (ValueError, OverflowError):
        collection_errors.append(
            {"metric": "cpu_temperature", "reason": "invalid_reading"}
        )
        return None
    if not -273.15 <= temperature <= 1000:
        collection_errors.append(
            {"metric": "cpu_temperature", "reason": "out_of_range"}
        )
        return None
    return temperature


def collect_platform_health(
    *,
    device_tree_paths: tuple[Path, ...] = (
        Path("/sys/firmware/devicetree/base"),
        Path("/proc/device-tree"),
    ),
    thermal_root: Path = Path("/sys/class/thermal"),
    uptime_path: Path = Path("/proc/uptime"),
    cpu_temperature_path: Path | None = None,
) -> dict[str, Any]:
    """Identify Pi 4/5 using kernel compatibility evidence, not CPU guesses."""
    errors: list[dict[str, str]] = []
    model: str | None = None
    compatible: list[str] = []
    for root in device_tree_paths:
        model = _text(root / "model", "platform_model", errors)
        raw = _text(root / "compatible", "platform_compatible", errors)
        compatible = raw.split("\0") if raw else []
        if model is not None or compatible:
            break
    supported = {
        "raspberrypi,4-model-b": "pi4",
        "raspberrypi,5-model-b": "pi5",
    }
    board = next((supported[item] for item in compatible if item in supported), None)
    state = (
        "supported"
        if board
        else ("unsupported" if model is not None or compatible else "unavailable")
    )
    if state != "supported":
        errors.append({"metric": "platform", "reason": f"{state}_platform"})

    temperature: float | None = None
    sensor: str | None = None
    if cpu_temperature_path is not None:
        temperature = read_temperature(cpu_temperature_path, collection_errors=errors)
        sensor = "configured" if temperature is not None else None
    else:
        try:
            zones = sorted(
                zone
                for zone in thermal_root.iterdir()
                if zone.name.startswith("thermal_zone")
            )
            for zone in zones:
                if _text(zone / "type", "cpu_temperature_sensor", errors) not in {
                    "cpu-thermal",
                    "bcm2835_thermal",
                }:
                    continue
                reading = read_temperature(zone / "temp", collection_errors=errors)
                if reading is not None:
                    temperature, sensor = reading, zone.name
                    break
        except OSError as error:
            errors.append(
                {
                    "metric": "cpu_temperature_sensor",
                    "reason": collection_error_reason(error),
                }
            )
    if temperature is None and not any(
        error["metric"] == "cpu_temperature" for error in errors
    ):
        errors.append({"metric": "cpu_temperature", "reason": "no_supported_sensor"})

    uptime: float | None = None
    raw_uptime = _text(uptime_path, "uptime", errors)
    try:
        if raw_uptime is not None:
            uptime = float(raw_uptime.split()[0])
            if not math.isfinite(uptime) or uptime < 0:
                uptime = None
                errors.append({"metric": "uptime", "reason": "out_of_range"})
    except (ValueError, IndexError):
        errors.append({"metric": "uptime", "reason": "invalid_reading"})

    return {
        "platform": {
            "state": state,
            "board": board,
            "model": model,
            "compatible": compatible,
        },
        "cpu_temperature_c": temperature,
        "cpu_temperature_sensor": sensor,
        "uptime_seconds": uptime,
        "collection_errors": errors,
        "capabilities": {
            "cpu_temperature": "available"
            if temperature is not None
            else "unavailable",
            "uptime": "available" if uptime is not None else "unavailable",
            "hardware_control": "unavailable",
        },
        "hardware_control_reason": (
            "GPIO, wake and camera power control require verified physical wiring"
        ),
    }

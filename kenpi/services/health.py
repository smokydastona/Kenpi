"""Best-effort host telemetry; unavailable readings are represented as null."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from kenpi.hardware.platform import (
    collect_platform_health,
    collection_error_reason,
)


def collect_host_health(
    *,
    meminfo_path: Path = Path("/proc/meminfo"),
    cpu_temperature_path: Path | None = None,
    storage_path: Path = Path("/"),
    device_tree_paths: tuple[Path, ...] = (
        Path("/sys/firmware/devicetree/base"),
        Path("/proc/device-tree"),
    ),
    thermal_root: Path = Path("/sys/class/thermal"),
    uptime_path: Path = Path("/proc/uptime"),
) -> dict[str, Any]:
    """Collect Linux host metrics without substituting mock or estimated values."""
    errors: list[dict[str, str]] = []
    memory: dict[str, int | None] = {
        "memory_total_bytes": None,
        "memory_available_bytes": None,
    }
    try:
        meminfo = meminfo_path.read_text(encoding="ascii")
    except (OSError, UnicodeError) as error:
        errors.append({"metric": "memory", "reason": collection_error_reason(error)})
    else:
        names = {
            "MemTotal": "memory_total_bytes",
            "MemAvailable": "memory_available_bytes",
        }
        for line in meminfo.splitlines():
            key, separator, remainder = line.partition(":")
            if not separator or key not in names:
                continue
            try:
                amount, unit = remainder.strip().split()
                value = int(amount)
            except ValueError:
                errors.append({"metric": names[key], "reason": "invalid_reading"})
                continue
            if unit != "kB" or value < 0:
                errors.append({"metric": names[key], "reason": "invalid_reading"})
                continue
            memory[names[key]] = value * 1024
        for metric, value in memory.items():
            if value is None and not any(error["metric"] == metric for error in errors):
                errors.append({"metric": metric, "reason": "missing_value"})

    hardware = collect_platform_health(
        device_tree_paths=device_tree_paths,
        thermal_root=thermal_root,
        uptime_path=uptime_path,
        cpu_temperature_path=cpu_temperature_path,
    )

    storage: dict[str, int | None] = {
        "storage_total_bytes": None,
        "storage_free_bytes": None,
    }
    try:
        usage = shutil.disk_usage(storage_path)
        storage["storage_total_bytes"] = usage.total
        storage["storage_free_bytes"] = usage.free
    except OSError as error:
        errors.append({"metric": "storage", "reason": collection_error_reason(error)})
    except ValueError:
        errors.append({"metric": "storage", "reason": "invalid_source"})
    errors.extend(hardware["collection_errors"])

    return {
        "application_status": "ok",
        "cpu_temperature_c": hardware["cpu_temperature_c"],
        "hardware": hardware,
        "collection_status": "partial" if errors else "complete",
        "collection_errors": errors,
        **memory,
        **storage,
    }

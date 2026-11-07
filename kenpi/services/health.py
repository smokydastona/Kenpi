"""Best-effort host telemetry; unavailable readings are represented as null."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any


def collect_host_health(
    *,
    meminfo_path: Path = Path("/proc/meminfo"),
    cpu_temperature_path: Path = Path("/sys/class/thermal/thermal_zone0/temp"),
    storage_path: Path = Path("/"),
) -> dict[str, Any]:
    """Collect Linux host metrics without substituting mock or estimated values."""
    memory: dict[str, int | None] = {
        "memory_total_bytes": None,
        "memory_available_bytes": None,
    }
    try:
        values: dict[str, int] = {}
        for line in meminfo_path.read_text(encoding="ascii").splitlines():
            key, separator, remainder = line.partition(":")
            if not separator or key not in {"MemTotal", "MemAvailable"}:
                continue
            amount, unit = remainder.strip().split()
            values[key] = int(amount) * (1024 if unit == "kB" else 1)
        memory["memory_total_bytes"] = values.get("MemTotal")
        memory["memory_available_bytes"] = values.get("MemAvailable")
    except (OSError, UnicodeError, ValueError):
        pass

    temperature: float | None = None
    try:
        temperature = (
            int(cpu_temperature_path.read_text(encoding="ascii").strip()) / 1000.0
        )
    except (OSError, UnicodeError, ValueError):
        pass

    storage: dict[str, int | None] = {
        "storage_total_bytes": None,
        "storage_free_bytes": None,
    }
    try:
        usage = shutil.disk_usage(storage_path)
        storage["storage_total_bytes"] = usage.total
        storage["storage_free_bytes"] = usage.free
    except OSError:
        pass

    return {
        "application_status": "ok",
        "cpu_temperature_c": temperature,
        **memory,
        **storage,
    }

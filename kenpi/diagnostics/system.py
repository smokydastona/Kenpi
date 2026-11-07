"""Raspberry Pi system evidence read from procfs/sysfs and the firmware throttle flags.

Every reading is None when the source is absent or unreadable; nothing is estimated.
Throttle bits are documented in the Raspberry Pi firmware docs (get_throttled).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Sequence

THROTTLE_BITS = {
    0: "under-voltage now",
    1: "frequency capped now",
    2: "throttled now",
    3: "soft temperature limit now",
    16: "under-voltage has occurred",
    17: "frequency capping has occurred",
    18: "throttling has occurred",
    19: "soft temperature limit has occurred",
}


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="ascii", errors="replace").strip()
    except OSError:
        return None


def read_throttled(
    sys_root: Path, vcgencmd: Sequence[str] = ("vcgencmd", "get_throttled")
) -> int | None:
    raw = _read(sys_root / "devices/platform/soc/soc:firmware/get_throttled")
    if raw is None:
        try:
            done = subprocess.run(
                list(vcgencmd), capture_output=True, text=True, timeout=3, check=False
            )
            raw = (
                done.stdout.strip().partition("=")[2] if done.returncode == 0 else None
            )
        except (OSError, subprocess.TimeoutExpired):
            raw = None
    try:
        return int(raw, 16) if raw else None
    except ValueError:
        return None


def collect_system(
    proc_root: Path = Path("/proc"),
    sys_root: Path = Path("/sys"),
    vcgencmd: Sequence[str] = ("vcgencmd", "get_throttled"),
    cpu_count: int | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "load_1m": None,
        "cpu_count": cpu_count or os.cpu_count(),
        "swap_total_bytes": None,
        "swap_used_bytes": None,
        "throttled_flags": None,
        "usb_devices": None,
    }
    load = _read(proc_root / "loadavg")
    if load:
        try:
            result["load_1m"] = float(load.split()[0])
        except (ValueError, IndexError):
            pass
    meminfo = _read(proc_root / "meminfo") or ""
    values = {}
    for line in meminfo.splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if key in ("SwapTotal", "SwapFree") and parts and parts[0].isdigit():
            values[key] = int(parts[0]) * 1024
    if len(values) == 2:
        result["swap_total_bytes"] = values["SwapTotal"]
        result["swap_used_bytes"] = values["SwapTotal"] - values["SwapFree"]
    result["throttled_flags"] = read_throttled(sys_root, vcgencmd)
    usb = sys_root / "bus/usb/devices"
    try:
        result["usb_devices"] = sorted(
            p.name
            for p in usb.iterdir()
            if ":" not in p.name and not p.name.startswith("usb")
        )
    except OSError:
        pass
    return result

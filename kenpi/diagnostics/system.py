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


REAL_FILESYSTEMS = {"ext4", "ext3", "ext2", "vfat", "f2fs", "xfs", "btrfs", "exfat"}


def readonly_mounts(mounts_text: str) -> list[str]:
    """Mount points of real filesystems that are mounted read-only.

    The kernel remounts ext4 read-only after I/O errors, so this is evidence
    of a filesystem fault (the cause still needs the kernel log).
    """
    found = []
    for line in mounts_text.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[2] in REAL_FILESYSTEMS:
            if "ro" in parts[3].split(","):
                found.append(parts[1])
    return sorted(found)


def collect_system(
    proc_root: Path = Path("/proc"),
    sys_root: Path = Path("/sys"),
    vcgencmd: Sequence[str] = ("vcgencmd", "get_throttled"),
    cpu_count: int | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "readonly_mounts": None,
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
    mounts = _read(proc_root / "mounts")
    if mounts is not None:
        result["readonly_mounts"] = readonly_mounts(mounts)
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


def _handshake_ages(
    interfaces: Sequence[str], wg_command: Sequence[str], now: float
) -> dict[str, float | None] | None:
    """Seconds since the newest peer handshake per interface (None: never, or unreadable).

    Peer public keys are deliberately not kept.
    """
    ages: dict[str, float | None] = {}
    for name in interfaces:
        try:
            done = subprocess.run(
                [*wg_command, name, "latest-handshakes"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if done.returncode != 0:
            return None
        stamps = []
        for line in done.stdout.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1].isdigit():
                stamps.append(int(parts[1]))
        newest = max(stamps, default=0)
        ages[name] = max(0.0, now - newest) if newest else None
    return ages


def collect_network(
    proc_root: Path = Path("/proc"),
    sys_root: Path = Path("/sys"),
    resolv_conf: Path = Path("/etc/resolv.conf"),
    cert_path: Path | None = None,
    now: float | None = None,
    wg_command: Sequence[str] = ("wg", "show"),
) -> dict[str, Any]:
    """Routing, resolver, WireGuard interface and gateway certificate evidence (None = unknown)."""
    import time

    result: dict[str, Any] = {
        "default_gateway": None,
        "has_default_route": None,
        "dns_servers": None,
        "wireguard_interfaces": None,
        "wireguard_handshake_age": None,
        "cert_days_left": None,
    }
    routes = _read(proc_root / "net/route")
    if routes is not None:
        result["has_default_route"] = False
        for line in routes.splitlines()[1:]:
            fields = line.split()
            if len(fields) > 2 and fields[1] == "00000000":
                try:
                    raw = bytes.fromhex(fields[2])[::-1]
                except ValueError:
                    continue
                result["has_default_route"] = True
                result["default_gateway"] = ".".join(str(b) for b in raw)
                break
    resolv = _read(resolv_conf)
    if resolv is not None:
        result["dns_servers"] = [
            line.split()[1]
            for line in resolv.splitlines()
            if line.startswith("nameserver") and len(line.split()) > 1
        ]
    try:
        names = [p.name for p in (sys_root / "class/net").iterdir()]
        result["wireguard_interfaces"] = sorted(
            n
            for n in names
            if (sys_root / "class/net" / n / "uevent").is_file()
            and "DEVTYPE=wireguard"
            in (_read(sys_root / "class/net" / n / "uevent") or "")
        )
    except OSError:
        pass
    if result["wireguard_interfaces"]:
        result["wireguard_handshake_age"] = _handshake_ages(
            result["wireguard_interfaces"], wg_command, now or time.time()
        )
    if cert_path is not None:
        try:
            from cryptography import x509

            cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
            expires = cert.not_valid_after_utc.timestamp()
            result["cert_days_left"] = (expires - (now or time.time())) / 86400
        except (OSError, ValueError, ImportError):
            pass
    return result

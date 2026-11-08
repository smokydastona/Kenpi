"""Raspberry Pi system evidence read from procfs/sysfs and the firmware throttle flags.

Every reading is None when the source is absent or unreadable; nothing is estimated.
Throttle bits are documented in the Raspberry Pi firmware docs (get_throttled).
"""

from __future__ import annotations

import os
import socket
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable, Sequence

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


def read_board(proc_root: Path, sys_root: Path) -> dict[str, Any]:
    """Board model, firmware-reported PSU limit and fan presence; None when not readable.

    `chosen/power/max_current` is read as a big-endian u32 in mA. That unit is taken from
    Raspberry Pi firmware documentation and has not been confirmed on this project's Pi.
    """
    model = None
    try:
        raw = (proc_root / "device-tree/model").read_bytes()
        model = raw.split(b"\0", 1)[0].decode("ascii", "replace").strip() or None
    except OSError:
        pass
    psu = None
    try:
        data = (proc_root / "device-tree/chosen/power/max_current").read_bytes()
        if len(data) == 4:
            psu = int.from_bytes(data, "big") or None
    except OSError:
        pass
    fan: bool | None = None
    try:
        kinds = [
            _read(p / "type")
            for p in (sys_root / "class/thermal").glob("cooling_device*")
        ]
        fan = any(k == "pwm-fan" for k in kinds)
    except OSError:
        pass
    return {"model": model, "psu_max_current_ma": psu, "fan_present": fan}


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
        "usb_details": None,
        "board": read_board(proc_root, sys_root),
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
    details = read_usb_devices(sys_root)
    if details is not None:
        result["usb_devices"] = [d["name"] for d in details]
        result["usb_details"] = details
    return result


def read_usb_devices(sys_root: Path) -> list[dict[str, Any]] | None:
    """USB devices (not root hubs) from sysfs: ids, product string, speed and driver binding.

    `unbound_interfaces` counts interfaces with no kernel driver attached; None when the
    device has no readable interface directories. Nothing is inferred about why.
    """
    usb = sys_root / "bus/usb/devices"
    try:
        entries = sorted(usb.iterdir())
    except OSError:
        return None
    devices: list[dict[str, Any]] = []
    for path in entries:
        if ":" in path.name or path.name.startswith("usb"):
            continue
        speed = _read(path / "speed")
        try:
            speed_mbps = float(speed) if speed else None
        except ValueError:
            speed_mbps = None
        interfaces = [p for p in path.glob(f"{path.name}:*") if p.is_dir()]
        devices.append(
            {
                "name": path.name,
                "vendor_id": _read(path / "idVendor"),
                "product_id": _read(path / "idProduct"),
                "product": (_read(path / "product") or "")[:64] or None,
                "speed_mbps": speed_mbps,
                "unbound_interfaces": (
                    sum(1 for i in interfaces if not (i / "driver").exists())
                    if interfaces
                    else None
                ),
            }
        )
    return devices


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


def _hostname_resolves(
    resolver: Callable[[str], Any], hostname: Callable[[], str], timeout: float = 3.0
) -> bool | None:
    """Whether the system resolver can resolve this host's own name (None = unknown)."""
    box: dict[str, bool] = {}

    def work() -> None:
        try:
            resolver(hostname())
            box["ok"] = True
        except socket.gaierror:
            box["ok"] = False
        except OSError:
            pass

    worker = threading.Thread(target=work, name="dns-check", daemon=True)
    worker.start()
    worker.join(timeout)
    return box.get("ok")


def collect_network(
    proc_root: Path = Path("/proc"),
    sys_root: Path = Path("/sys"),
    resolv_conf: Path = Path("/etc/resolv.conf"),
    cert_path: Path | None = None,
    now: float | None = None,
    wg_command: Sequence[str] = ("wg", "show"),
    resolver: Callable[[str], Any] | None = None,
    hostname: Callable[[], str] = socket.gethostname,
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
        "hostname_resolves": None,
    }
    result["hostname_resolves"] = _hostname_resolves(
        resolver or socket.gethostbyname, hostname
    )
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

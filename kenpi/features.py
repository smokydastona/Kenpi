"""User-controlled feature toggles. Features are off unless explicitly enabled.

The flag file is written only by a privileged operator (kenpi-features) and merely read by the
unprivileged service, so the service can never enable a feature for itself.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

FEATURES: dict[str, str] = {
    "assistant": "Voice, AI assistant and Diagnostics & AI Support",
}
DEFAULT_STATE_DIRECTORY = Path("/var/lib/kenpi")
FLAG_FILE = "features.json"
SERVICE = "kenpi-status.service"


class FeatureStore:
    def __init__(self, state_directory: Path) -> None:
        self._path = state_directory / FLAG_FILE

    def _read(self) -> dict[str, bool]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {k: v for k, v in data.items() if k in FEATURES and v is True}

    def is_enabled(self, name: str) -> bool:
        """Missing, unreadable or malformed state means disabled."""
        return name in FEATURES and self._read().get(name, False)

    def set_enabled(self, name: str, enabled: bool) -> None:
        if name not in FEATURES:
            raise ValueError(f"unknown feature: {name}")
        values = self._read()
        if enabled:
            values[name] = True
        else:
            values.pop(name, None)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(values, sort_keys=True), encoding="utf-8")
        tmp.chmod(0o644)
        tmp.replace(self._path)

    def describe(self) -> dict[str, dict[str, object]]:
        return {
            n: {"enabled": self.is_enabled(n), "description": d}
            for n, d in FEATURES.items()
        }


def prepare_data_directory(state_directory: Path, user: str = "kenpi") -> None:
    """Create the writable assistant data directory and hand it to the service user."""
    data = state_directory / "assistant"
    data.mkdir(parents=True, exist_ok=True)
    if os.geteuid() == 0:
        try:
            shutil.chown(data, user=user, group=user)
        except (LookupError, OSError):
            logger.warning("Could not assign %s to %s", data, user)
    data.chmod(0o700)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kenpi-features", description="Turn optional Kenpi features on or off"
    )
    parser.add_argument("--state-directory", type=Path, default=DEFAULT_STATE_DIRECTORY)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="show feature states")
    for command in ("enable", "disable"):
        item = sub.add_parser(command)
        item.add_argument("feature", choices=sorted(FEATURES))
        item.add_argument(
            "--apply",
            action="store_true",
            help=f"restart {SERVICE} so the change takes effect now",
        )
    args = parser.parse_args(argv)
    store = FeatureStore(args.state_directory)
    if args.command == "status":
        for name, info in store.describe().items():
            print(
                f"{name}: {'enabled' if info['enabled'] else 'disabled'} - {info['description']}"
            )
        return 0
    enabled = args.command == "enable"
    try:
        if enabled:
            prepare_data_directory(args.state_directory)
        store.set_enabled(args.feature, enabled)
    except OSError as error:
        print(
            f"cannot change feature state ({type(error).__name__}); run as root",
            file=sys.stderr,
        )
        return 1
    print(f"{args.feature} {'enabled' if enabled else 'disabled'}")
    if args.apply:
        systemctl = shutil.which("systemctl")
        if (
            systemctl is None
            or subprocess.run([systemctl, "restart", SERVICE]).returncode != 0
        ):
            print(
                f"restart {SERVICE} manually for the change to take effect",
                file=sys.stderr,
            )
            return 1
    else:
        print(f"takes effect when {SERVICE} next starts (or use --apply)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

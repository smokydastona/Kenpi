"""Backup/restore CLI. Restore must run as root (it swaps /etc and state directories)."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from kenpi import __version__
from kenpi.core.config import KenpiConfig, load_config
from kenpi.core.errors import ConfigurationError
from kenpi.recovery.backup import (
    MEDIA,
    apply_retention,
    create_backup,
    fetch,
    list_backups,
    restore_backup,
    verify_archive,
)
from kenpi.recovery.destinations import (
    BackupDestination,
    BackupError,
    LocalDirectoryDestination,
    SftpDestination,
)

SERVICE = "kenpi-status.service"


def parse_destination(spec: str, identity: str, known_hosts: str) -> BackupDestination:
    if spec.startswith("local:"):
        return LocalDirectoryDestination(spec[len("local:") :])
    url = urlparse(spec)
    if url.scheme == "sftp" and url.hostname and url.username:
        return SftpDestination(
            url.hostname,
            url.username,
            url.path or "/",
            identity,
            known_hosts,
            url.port or 22,
        )
    raise BackupError("destination must be local:/path or sftp://user@host[:port]/path")


def roots_for(config: KenpiConfig, config_path: Path) -> dict[str, Path]:
    roots = {"config": config_path.parent, "state": Path(config.state_directory)}
    if config.media_directory:
        roots[MEDIA] = Path(config.media_directory)
    return roots


def _health(port: int, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/healthz", timeout=3
            ) as r:
                if r.status == 200:
                    return True
        except OSError:
            pass
        time.sleep(1)
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kenpi-backup")
    parser.add_argument("--config", type=Path, default=Path("/etc/kenpi/status.toml"))
    parser.add_argument("--dest", required=True)
    parser.add_argument("--identity", default=os.environ.get("KENPI_SFTP_IDENTITY", ""))
    parser.add_argument(
        "--known-hosts", default=os.environ.get("KENPI_SFTP_KNOWN_HOSTS", "")
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    create = sub.add_parser("create")
    create.add_argument("--include-media", action="store_true")
    create.add_argument("--prune", type=int, metavar="KEEP")
    sub.add_parser("list")
    sub.add_parser("clean-partials")
    sub.add_parser("verify").add_argument("name")
    prune = sub.add_parser("prune")
    prune.add_argument("--keep", type=int, required=True)
    restore = sub.add_parser("restore")
    restore.add_argument("name")
    restore.add_argument(
        "--confirm", action="store_true", help="apply (default is a dry run)"
    )
    restore.add_argument("--include-media", action="store_true")
    restore.add_argument(
        "--recovery-dir", type=Path, default=Path("/var/lib/kenpi-recovery")
    )
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
        dest = parse_destination(args.dest, args.identity, args.known_hosts)
        roots = roots_for(config, args.config)
        if args.cmd == "create":
            exclude = tuple(
                Path(d.directory)
                for d in [dest]
                if isinstance(d, LocalDirectoryDestination)
            )
            info = create_backup(roots, dest, __version__, args.include_media, exclude)
            print(
                json.dumps(
                    {
                        "name": info.name,
                        "files": info.files,
                        "bytes": info.bytes,
                        "includes_media": info.includes_media,
                    }
                )
            )
            if args.prune:
                apply_retention(dest, args.prune)
        elif args.cmd == "list":
            print(json.dumps(list_backups(dest), indent=1))
        elif args.cmd == "clean-partials":
            for name in dest.list_partials():
                dest.remove_partial(name)
                print("removed", name)
        elif args.cmd == "prune":
            for name in apply_retention(dest, args.keep):
                print("deleted", name)
        else:
            with tempfile.TemporaryDirectory(prefix="kenpi-fetch-") as tmp:
                archive = fetch(dest, args.name, Path(tmp))
                if args.cmd == "verify":
                    manifest = verify_archive(archive)
                    print(json.dumps({"ok": True, "kenpi_version": manifest.kenpi_version,
                                      "created_at": manifest.created_at, "components": manifest.components}))  # fmt: skip
                    return 0
                return _restore(args, config, roots, archive)
    except (BackupError, ConfigurationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def _restore(args, config: KenpiConfig, roots: dict[str, Path], archive: Path) -> int:
    from kenpi.security.devices import DeviceRegistry

    config_name = args.config.name

    def validate(staged: dict[str, Path]) -> None:
        candidate = staged["config"] / config_name
        if candidate.exists():
            load_config(candidate)  # raises ConfigurationError for an unusable config

    def after() -> None:
        gateway = Path(config.state_directory) / "gateway"
        if gateway.is_dir():
            count = DeviceRegistry(gateway).revoke_all(
                "restore: fresh enrolment required"
            )
            print(f"revoked {count} paired device(s); re-enrol them")

    def restart() -> None:
        subprocess.run(["systemctl", "restart", SERVICE], check=True, timeout=60)

    report = restore_backup(
        archive, roots, __version__, args.recovery_dir,
        dry_run=not args.confirm, confirm=args.confirm, include_media=args.include_media,
        validate_staged=validate, restart_services=restart,
        health_check=lambda: _health(config.api_port),
        after_restore=after,
    )  # fmt: skip
    print(json.dumps({"status": report.status, "reason": report.reason, "warnings": report.warnings,
                      "overwrites": {k: len(v) for k, v in report.overwrites.items()},
                      "media_skipped": len(report.media_skipped),
                      "snapshot": str(report.snapshot) if report.snapshot else None}))  # fmt: skip
    return 0 if report.status in ("dry-run", "restored") else 1


if __name__ == "__main__":
    sys.exit(main())

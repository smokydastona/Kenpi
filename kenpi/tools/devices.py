"""Local administrator CLI for pairing, device management and remote access.

Run as the service account (it needs the state directory). Approving a device here is the
explicit administrator approval required by ADR 0002.
"""

from __future__ import annotations

import argparse
import base64
import json
import shutil
import subprocess
import sys
from pathlib import Path

from kenpi.core.config import load_config
from kenpi.remote.wireguard import RemoteAccess, RemoteState, ensure_server_key
from kenpi.security.devices import DEFAULT_SCOPES, AuthError, DeviceRegistry
from kenpi.security.tls import ensure_certificate


def pairing_payload(
    host: str, port: int, fingerprint: str, eid: str, challenge: str, expires: float
) -> str:
    body = {"v": 1, "address": host, "port": port, "fingerprint": fingerprint,
            "enrollment_id": eid, "challenge": challenge, "expires": expires}  # fmt: skip
    encoded = base64.urlsafe_b64encode(json.dumps(body, separators=(",", ":")).encode())
    return "kenpi://pair?d=" + encoded.decode().rstrip("=")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kenpi-devices")
    parser.add_argument("--config", type=Path, default=Path("/etc/kenpi/kenpi.toml"))
    sub = parser.add_subparsers(dest="cmd", required=True)
    pair = sub.add_parser("pair", help="start pairing and print the QR payload")
    pair.add_argument("--scopes", default=",".join(sorted(DEFAULT_SCOPES)))
    pair.add_argument("--allow-remote", action="store_true")
    pair.add_argument(
        "--host", help="gateway address to embed (default: first gateway host)"
    )
    sub.add_parser("pending")
    for name in ("approve", "deny"):
        sub.add_parser(name).add_argument("enrollment_id")
    sub.add_parser("list")
    for name in ("revoke", "allow-remote", "deny-remote", "reenroll"):
        sub.add_parser(name).add_argument("device_id")
    remote = sub.add_parser("remote")
    remote.add_argument(
        "action", choices=["status", "enable", "disable", "render", "apply", "init-key"]
    )
    args = parser.parse_args(argv)

    config = load_config(args.config)
    state_dir = Path(config.state_directory) / "gateway"
    registry = DeviceRegistry(state_dir)
    try:
        if args.cmd == "pair":
            if not config.gateway_enabled:
                print("error: gateway_enabled is false", file=sys.stderr)
                return 2
            host = args.host or config.gateway_hosts[0]
            if host not in config.gateway_hosts:
                print("error: --host must be one of gateway_hosts", file=sys.stderr)
                return 2
            _, _, fingerprint = ensure_certificate(
                state_dir, list(config.gateway_hosts)
            )
            eid, challenge, expires = registry.begin_pairing(
                frozenset(s for s in args.scopes.split(",") if s), args.allow_remote
            )
            payload = pairing_payload(
                host, config.gateway_port, fingerprint, eid, challenge, expires
            )
            print(payload)
            if shutil.which("qrencode"):
                subprocess.run(["qrencode", "-t", "ANSIUTF8", payload], check=False)
            print(
                "Expires in 5 minutes. Run 'pending' then 'approve' once the device submits."
            )
        elif args.cmd == "pending":
            for item in registry.pending():
                print(json.dumps(item))
        elif args.cmd == "approve":
            print("approved device", registry.approve(args.enrollment_id))
        elif args.cmd == "deny":
            registry.deny(args.enrollment_id)
        elif args.cmd == "list":
            for item in registry.list_devices():
                print(json.dumps(item))
        elif args.cmd == "revoke":
            registry.revoke(args.device_id)
            _sync_remote(config, registry, state_dir)
        elif args.cmd in ("allow-remote", "deny-remote"):
            registry.set_remote(args.device_id, args.cmd == "allow-remote")
            _sync_remote(config, registry, state_dir)
        elif args.cmd == "reenroll":
            old = next(
                (
                    d
                    for d in registry.list_devices()
                    if d["device_id"] == args.device_id
                ),
                None,
            )
            if old is None:
                raise AuthError("unknown_device")
            registry.revoke(args.device_id, "re-enrolment")
            print("revoked; run 'pair' with the same scopes:", ",".join(old["scopes"]))  # type: ignore[arg-type]
        elif args.cmd == "remote":
            return _remote(args.action, config, registry, state_dir)
    except AuthError as exc:
        print(f"error: {exc.code}", file=sys.stderr)
        return 1
    return 0


def _access(config, registry, state_dir) -> RemoteAccess:
    if not config.wireguard_network:
        raise AuthError("wireguard_network_not_configured")
    return RemoteAccess(
        registry, RemoteState(state_dir), config.wireguard_network, config.wireguard_port,
        state_dir / "wireguard" / "server.key",
    )  # fmt: skip


def _sync_remote(config, registry, state_dir) -> None:
    """Drop revoked/changed peers from a running tunnel immediately."""
    if RemoteState(state_dir).enabled() and config.wireguard_network:
        _access(config, registry, state_dir).apply()


def _remote(action, config, registry, state_dir) -> int:
    state = RemoteState(state_dir)
    if action == "status":
        print(json.dumps({"enabled": state.enabled()}))
        return 0
    access = _access(config, registry, state_dir)
    if action == "init-key":
        print(ensure_server_key(state_dir / "wireguard" / "server.key"))
    elif action == "render":
        sys.stdout.write(access.write_config())
    elif action == "enable":
        access.enable()
    elif action == "disable":
        access.disable()
    elif action == "apply":
        access.apply()
    return 0


if __name__ == "__main__":
    sys.exit(main())

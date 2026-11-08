"""Privileged-operation broker: the only path from the phone to Pi administration.

There is no shell and no free-form command. Each operation is declared here with a
required scope, a risk level, strict argument validation and a timeout. Operations at
risk "confirm" run only on a second request carrying a single-use token that was issued
for the same device, operation and arguments. Every attempt is audited.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONFIRM_TTL_SECONDS = 60.0
MAX_PENDING = 32
UNIT = re.compile(r"^[a-z0-9][a-z0-9@_.-]{0,62}\.(service|timer)$")

Runner = Callable[[list[str], float], tuple[int, str]]


class BrokerError(Exception):
    def __init__(self, code: str, status: int = 400) -> None:
        super().__init__(code)
        self.code, self.status = code, status


def subprocess_runner(argv: list[str], timeout: float) -> tuple[int, str]:
    try:
        done = subprocess.run(  # noqa: S603 - argv is built only from validated values
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return 127, "command not found"
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    return done.returncode, (done.stdout + done.stderr).strip()[:2000]


@dataclass(frozen=True, slots=True)
class Operation:
    id: str
    description: str
    scope: str
    risk: str  # "read" or "confirm"
    handler: Callable[[dict[str, Any]], dict[str, Any]]
    validate: Callable[[dict[str, Any]], dict[str, Any]]
    timeout: float = 10.0

    def describe(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "scope": self.scope,
            "risk": self.risk,
        }


@dataclass(slots=True)
class _Pending:
    digest: str
    expires: float


@dataclass(slots=True)
class Broker:
    audit: Callable[..., None]
    allowed_units: frozenset[str]
    runner: Runner = subprocess_runner
    proc_root: Path = Path("/proc")
    clock: Callable[[], float] = time.time
    _ops: dict[str, Operation] = field(default_factory=dict)
    _pending: dict[str, _Pending] = field(default_factory=dict)

    def __post_init__(self) -> None:
        bad = [u for u in self.allowed_units if not UNIT.match(u)]
        if bad:
            raise ValueError(f"invalid unit names: {sorted(bad)}")
        self._register_defaults()

    def operations(self, scopes: frozenset[str]) -> list[dict[str, Any]]:
        return [o.describe() for o in self._ops.values() if o.scope in scopes]

    def run(
        self,
        device_id: str,
        scopes: frozenset[str],
        op_id: str,
        args: object,
        confirm: str | None = None,
    ) -> dict[str, Any]:
        op = self._ops.get(op_id)
        if op is None:
            raise BrokerError("unknown_operation", 404)
        if op.scope not in scopes:
            self.audit("op_denied", device=device_id, op=op_id, reason="scope")
            raise BrokerError("scope_required", 403)
        if not isinstance(args, dict):
            raise BrokerError("bad_arguments")
        try:
            clean = op.validate(args)
        except (KeyError, TypeError, ValueError) as exc:
            self.audit("op_rejected", device=device_id, op=op_id)
            raise BrokerError("bad_arguments") from exc
        if op.risk == "confirm":
            digest = self._digest(device_id, op_id, clean)
            if confirm is None:
                return self._challenge(device_id, op_id, digest)
            self._consume(confirm, digest)
        self.audit("op_start", device=device_id, op=op_id, args=clean)
        try:
            result = op.handler(clean)
        except BrokerError:
            raise
        except Exception as exc:
            self.audit("op_error", device=device_id, op=op_id, error=type(exc).__name__)
            raise BrokerError("operation_failed", 500) from exc
        self.audit("op_done", device=device_id, op=op_id, ok=result.get("ok", True))
        return {"status": "done", "operation": op_id, "result": result}

    # ---- confirmation -----------------------------------------------------------

    @staticmethod
    def _digest(device_id: str, op_id: str, args: dict[str, Any]) -> str:
        blob = json.dumps([device_id, op_id, args], sort_keys=True).encode()
        return hashlib.sha256(blob).hexdigest()

    def _challenge(self, device_id: str, op_id: str, digest: str) -> dict[str, Any]:
        now = self.clock()
        self._pending = {k: v for k, v in self._pending.items() if v.expires > now}
        if len(self._pending) >= MAX_PENDING:
            raise BrokerError("too_many_pending", 429)
        token = secrets.token_urlsafe(24)
        self._pending[token] = _Pending(digest, now + CONFIRM_TTL_SECONDS)
        self.audit("op_confirmation_issued", device=device_id, op=op_id)
        return {
            "status": "confirmation_required",
            "operation": op_id,
            "confirm_token": token,
            "expires_in": int(CONFIRM_TTL_SECONDS),
        }

    def _consume(self, token: str, digest: str) -> None:
        pending = self._pending.pop(token, None)
        if (
            pending is None
            or pending.expires <= self.clock()
            or not hmac.compare_digest(pending.digest, digest)
        ):
            raise BrokerError("confirmation_invalid", 403)

    # ---- operations -------------------------------------------------------------

    def _unit_args(self, args: dict[str, Any]) -> dict[str, Any]:
        unit = args["unit"]
        if not isinstance(unit, str) or unit not in self.allowed_units:
            raise ValueError("unit not allowed")
        return {"unit": unit}

    def _register_defaults(self) -> None:
        def no_args(args: dict[str, Any]) -> dict[str, Any]:
            if args:
                raise ValueError("no arguments expected")
            return {}

        def service_status(args: dict[str, Any]) -> dict[str, Any]:
            code, out = self.runner(["systemctl", "is-active", args["unit"]], 5.0)
            return {
                "unit": args["unit"],
                "active": out.strip() or "unknown",
                "ok": code == 0,
            }

        def service_restart(args: dict[str, Any]) -> dict[str, Any]:
            code, out = self.runner(["systemctl", "restart", args["unit"]], 25.0)
            return {"unit": args["unit"], "ok": code == 0, "detail": out}

        def power(verb: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
            def handler(_: dict[str, Any]) -> dict[str, Any]:
                code, out = self.runner(["systemctl", verb], 10.0)
                return {"ok": code == 0, "detail": out}

            return handler

        def list_services(_: dict[str, Any]) -> dict[str, Any]:
            return {"units": sorted(self.allowed_units)}

        def processes(_: dict[str, Any]) -> dict[str, Any]:
            return {"processes": self._read_processes()}

        for op in (
            Operation(
                "list_services",
                "Services Kenpi may manage",
                "ops.read",
                "read",
                list_services,
                no_args,
            ),
            Operation(
                "service_status",
                "Is a Kenpi service running",
                "ops.read",
                "read",
                service_status,
                self._unit_args,
                6.0,
            ),
            Operation(
                "process_list",
                "Top processes by memory",
                "ops.read",
                "read",
                processes,
                no_args,
            ),
            Operation(
                "service_restart",
                "Restart a Kenpi service",
                "ops.manage",
                "confirm",
                service_restart,
                self._unit_args,
                26.0,
            ),
            Operation(
                "reboot",
                "Reboot the Pi (the dashcam keeps recording on its own)",
                "ops.manage",
                "confirm",
                power("reboot"),
                no_args,
            ),
            Operation(
                "shutdown",
                "Power off the Pi; it must be restarted by hand or by vehicle power",
                "ops.manage",
                "confirm",
                power("poweroff"),
                no_args,
            ),
        ):
            self._ops[op.id] = op

    def _read_processes(self, limit: int = 15) -> list[dict[str, Any]]:
        page = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096
        found = []
        for entry in self.proc_root.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                name = (entry / "comm").read_text().strip()
                resident = int((entry / "statm").read_text().split()[1]) * page
            except (OSError, ValueError, IndexError):
                continue
            found.append(
                {"pid": int(entry.name), "name": name[:32], "rss_bytes": resident}
            )
        found.sort(key=lambda p: -p["rss_bytes"])
        return found[:limit]

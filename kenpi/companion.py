"""Companion-phone awareness and log exchange.

Presence comes from the gateway's live sessions (what Kenpi can verify). Phone logs are
untrusted input: bounded, redacted, kept in memory only, and used purely as diagnostic
evidence. Nothing a phone sends can trigger an action.
"""

from __future__ import annotations

import re
import time
from typing import Any, Callable

MAX_LINES = 200
MAX_LINE_CHARS = 300
MAX_DEVICES = 16

_SECRETS = (
    re.compile(r"(?i)\bbearer\s+\S+"),
    re.compile(r"(?i)\b(token|secret|password|signature|challenge|key)(=|:)\s*\S+"),
    re.compile(r"kenpi://\S+"),
    re.compile(r"[A-Za-z0-9+/_=-]{32,}"),
)
_PROBLEM = re.compile(
    r"(?i)disconnect|revoked|denied|refused|timed? ?out|unreachable|expired|certificate|failed"
)


def redact(line: str) -> str:
    """Strip anything token-, key- or pairing-shaped, then cap the length."""
    for pattern in _SECRETS:
        line = pattern.sub(
            lambda m: "[redacted]"
            if m.lastindex is None
            else f"{m.group(1)}=[redacted]",
            line,
        )
    return "".join(c for c in line if c.isprintable())[:MAX_LINE_CHARS]


class CompanionRegistry:
    def __init__(
        self,
        presence: Callable[[], list[dict[str, Any]]] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._presence = presence
        self._clock = clock
        self._reports: dict[str, dict[str, Any]] = {}

    def set_presence(self, presence: Callable[[], list[dict[str, Any]]]) -> None:
        self._presence = presence

    def connected(self) -> list[dict[str, Any]] | None:
        """None means Kenpi has no way to know (gateway not running)."""
        if self._presence is None:
            return None
        try:
            return list(self._presence())
        except Exception:
            return None

    def report(self, device_id: str, name: str, payload: object) -> int:
        """Validate and store a phone's log report; returns the number of lines kept."""
        if not isinstance(payload, dict):
            raise ValueError("JSON object required")
        lines = payload.get("lines", [])
        if not isinstance(lines, list) or any(not isinstance(x, str) for x in lines):
            raise ValueError("lines must be a list of strings")
        version, state = payload.get("app_version", ""), payload.get("connection", "")
        if not isinstance(version, str) or not isinstance(state, str):
            raise ValueError("app_version and connection must be strings")
        if device_id not in self._reports and len(self._reports) >= MAX_DEVICES:
            raise ValueError("too many companion devices")
        kept = [redact(line) for line in lines[-MAX_LINES:]]
        self._reports[device_id] = {
            "name": redact(name)[:64],
            "received_at": self._clock(),
            "app_version": redact(version)[:32],
            "connection": redact(state)[:64],
            "lines": kept,
        }
        return len(kept)

    def latest_lines(self, device_id: str) -> list[str]:
        return list(self._reports.get(device_id, {}).get("lines", []))

    def evidence(self) -> dict[str, Any]:
        """Diagnostic view: live presence plus each phone's last report."""
        connected = self.connected()
        ids = {d["device_id"] for d in connected or []}
        reports = []
        for device_id, report in self._reports.items():
            reports.append(
                {
                    "device_id": device_id,
                    "name": report["name"],
                    "connected_now": device_id in ids,
                    "age_seconds": round(self._clock() - report["received_at"]),
                    "app_version": report["app_version"],
                    "phone_connection": report["connection"],
                    "problem_lines": [
                        line for line in report["lines"] if _PROBLEM.search(line)
                    ][-5:],
                }
            )
        return {
            "presence_known": connected is not None,
            "connected_count": None if connected is None else len(connected),
            "connected": connected or [],
            "reports": reports,
        }

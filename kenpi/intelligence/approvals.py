"""Single-use approvals bound to one tool, one argument set and one session, with an expiry.

Approvals live in memory only: a restart discards every pending request, which fails safe.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

MAX_PENDING = 32


def args_digest(tool: str, args: dict[str, Any]) -> str:
    canonical = json.dumps(
        [tool, args], sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class PendingApproval:
    approval_id: str
    session_id: str
    tool: str
    args: dict[str, Any]
    digest: str
    expires_at: float


class ApprovalStore:
    def __init__(
        self, ttl_seconds: float = 120.0, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._pending: dict[str, PendingApproval] = {}
        self._lock = threading.Lock()

    def _purge(self) -> None:
        now = self._clock()
        for key in [k for k, v in self._pending.items() if v.expires_at <= now]:
            del self._pending[key]

    def request(
        self, session_id: str, tool: str, args: dict[str, Any]
    ) -> PendingApproval:
        with self._lock:
            self._purge()
            digest = args_digest(tool, args)
            for existing in self._pending.values():
                if (existing.session_id, existing.digest) == (session_id, digest):
                    return existing
            if len(self._pending) >= MAX_PENDING:
                oldest = min(self._pending.values(), key=lambda p: p.expires_at)
                del self._pending[oldest.approval_id]
            pending = PendingApproval(
                secrets.token_urlsafe(12),
                session_id,
                tool,
                dict(args),
                digest,
                self._clock() + self._ttl,
            )
            self._pending[pending.approval_id] = pending
            return pending

    def consume(self, approval_id: str) -> PendingApproval | None:
        """Atomically remove and return a live approval; a second call returns None."""
        with self._lock:
            self._purge()
            pending = self._pending.pop(approval_id, None)
            if pending is None or pending.digest != args_digest(
                pending.tool, pending.args
            ):
                return None
            return pending

    def list(self) -> list[PendingApproval]:
        with self._lock:
            self._purge()
            return list(self._pending.values())

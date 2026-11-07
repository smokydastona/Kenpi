"""Non-sensitive operator preferences and short-lived conversation sessions."""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

ALLOWED_PREFERENCES = frozenset(
    {
        "voice_profile",
        "language",
        "speech_speed",
        "volume",
        "dashboard_screen",
        "response_style",
        "character",
    }
)
_VALUE = re.compile(r"^[\w.\- ]{1,64}$")


class PreferenceMemory:
    """Whitelisted keys only, bounded values, explicit deletion; persistence is best-effort."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._values: dict[str, str] = {}
        if path is not None:
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self._values = {
                        k: v
                        for k, v in loaded.items()
                        if k in ALLOWED_PREFERENCES
                        and isinstance(v, str)
                        and _VALUE.match(v)
                    }
            except (OSError, ValueError):
                pass

    def set(self, key: str, value: str) -> None:
        if key not in ALLOWED_PREFERENCES:
            raise ValueError(f"unsupported preference: {key}")
        if not isinstance(value, str) or not _VALUE.match(value):
            raise ValueError("invalid preference value")
        self._values[key] = value
        self._save()

    def get(self, key: str) -> str | None:
        return self._values.get(key)

    def all(self) -> dict[str, str]:
        return dict(self._values)

    def clear(self) -> None:
        self._values.clear()
        if self._path is not None:
            try:
                self._path.unlink(missing_ok=True)
            except OSError:
                logger.warning("Preference file not removed")

    def _save(self) -> None:
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._values), encoding="utf-8")
            tmp.chmod(0o600)
            tmp.replace(self._path)
        except OSError as error:
            logger.warning("Preferences not persisted: %s", type(error).__name__)


class SessionStore:
    """Pending clarification state per session, expiring after ttl seconds."""

    def __init__(
        self,
        ttl_seconds: float = 120.0,
        max_sessions: int = 16,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl_seconds
        self._max = max_sessions
        self._clock = clock
        self._sessions: dict[str, tuple[float, dict[str, Any]]] = {}

    def _expire(self) -> None:
        now = self._clock()
        for key in [k for k, (t, _) in self._sessions.items() if now - t > self._ttl]:
            del self._sessions[key]

    def pending(self, session_id: str) -> dict[str, Any] | None:
        self._expire()
        entry = self._sessions.get(session_id)
        return entry[1] if entry else None

    def set_pending(self, session_id: str, pending: dict[str, Any] | None) -> None:
        self._expire()
        if pending is None:
            self._sessions.pop(session_id, None)
            return
        if session_id not in self._sessions and len(self._sessions) >= self._max:
            del self._sessions[min(self._sessions, key=lambda k: self._sessions[k][0])]
        self._sessions[session_id] = (self._clock(), pending)

    def end(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

"""Bounded, searchable, redacted diagnostic log records from several sources.

Sources are labelled. Records from `android` (and any source in UNTRUSTED) are evidence about the
phone only: they are searchable but never fed to the fault analyzer as if Kenpi observed them,
and nothing in them is ever interpreted as an instruction.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Sequence

from kenpi.companion import redact

SOURCES = frozenset(
    {"app", "journal", "gateway", "android", "firmware", "voice", "network"}
)
UNTRUSTED = frozenset({"android"})
LEVELS = {0: "CRITICAL", 1: "CRITICAL", 2: "CRITICAL", 3: "ERROR", 4: "WARNING"}
MAX_RECORDS = 5000
MAX_QUERY = 100


@dataclass(frozen=True, slots=True)
class LogRecord:
    timestamp: float
    source: str
    level: str
    message: str

    def describe(self) -> dict[str, object]:
        return {
            "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.timestamp)),
            "source": self.source,
            "level": self.level,
            "message": self.message,
            "trusted": self.source not in UNTRUSTED,
        }

    def as_line(self) -> str:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(self.timestamp))
        return f"{stamp} {self.level} {self.source} {self.message}"


class JournalUnavailable(Exception):
    pass


class LogStore:
    def __init__(
        self, capacity: int = MAX_RECORDS, clock: Callable[[], float] = time.time
    ) -> None:
        self._records: deque[LogRecord] = deque(maxlen=capacity)
        self._clock = clock
        self._lock = threading.Lock()

    def ingest(
        self,
        source: str,
        lines: Sequence[str],
        level: str = "INFO",
        timestamp: float | None = None,
        dedupe: bool = False,
    ) -> int:
        if source not in SOURCES:
            raise ValueError(f"unknown log source: {source}")
        now = timestamp if timestamp is not None else self._clock()
        records = [
            LogRecord(now, source, level, redact(line))
            for line in lines
            if isinstance(line, str) and line.strip()
        ]
        with self._lock:
            if dedupe:
                seen = {(r.source, r.message) for r in self._records}
                records = [r for r in records if (r.source, r.message) not in seen]
            self._records.extend(records)
        return len(records)

    def add_records(self, records: Sequence[LogRecord]) -> None:
        """Add records not already stored (same source, time and message)."""
        with self._lock:
            seen = {(r.source, r.timestamp, r.message) for r in self._records}
            self._records.extend(
                r for r in records if (r.source, r.timestamp, r.message) not in seen
            )

    def search(
        self,
        query: str = "",
        source: str | None = None,
        level: str | None = None,
        limit: int = 50,
    ) -> list[LogRecord]:
        needle = query.strip().lower()[:MAX_QUERY]
        with self._lock:
            snapshot = list(self._records)
        found = [
            r
            for r in reversed(snapshot)
            if (source is None or r.source == source)
            and (level is None or r.level == level)
            and (not needle or needle in r.message.lower())
        ]
        return found[: max(1, min(limit, 200))][::-1]

    def analyzer_lines(self) -> list[str]:
        """Only what Kenpi itself observed; phone-provided records are excluded."""
        with self._lock:
            return [r.as_line() for r in self._records if r.source not in UNTRUSTED]

    def counts(self) -> dict[str, int]:
        with self._lock:
            result: dict[str, int] = {}
            for r in self._records:
                result[r.source] = result.get(r.source, 0) + 1
            return result


def read_journal(
    units: Sequence[str],
    lines: int = 200,
    command: Sequence[str] = ("journalctl",),
    timeout: float = 5.0,
) -> list[LogRecord]:
    """Read recent systemd journal entries. Raises JournalUnavailable rather than returning nothing."""
    argv = [*command, "--no-pager", "-o", "json", "-n", str(max(1, min(lines, 1000)))]
    for unit in units:
        if not unit.replace("-", "").replace(".", "").replace("@", "").isalnum():
            raise ValueError(f"invalid unit name: {unit!r}")
        argv += ["-u", unit]
    try:
        done = subprocess.run(
            argv, capture_output=True, timeout=timeout, text=True, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise JournalUnavailable(type(error).__name__) from error
    if done.returncode != 0:
        raise JournalUnavailable(
            redact(done.stderr.strip())[:200] or "journalctl failed"
        )
    records = []
    for raw in done.stdout.splitlines():
        try:
            entry = json.loads(raw)
            message = entry["MESSAGE"]
            if not isinstance(message, str):
                continue
            micros = int(entry.get("__REALTIME_TIMESTAMP", 0))
            priority = int(entry.get("PRIORITY", 6))
        except (ValueError, KeyError, TypeError):
            continue
        records.append(
            LogRecord(
                micros / 1e6 or time.time(),
                "journal",
                LEVELS.get(priority, "INFO"),
                redact(message),
            )
        )
    return records

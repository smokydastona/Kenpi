"""Log parsing and correlation against known failure signatures."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

_LINE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})(?:[.,]\d+)?\s+"
    r"(?P<level>DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+(?P<source>\S+)\s+(?P<message>.*)$"
)

# Matches are evidence only; faults are decided in faults.py.
SIGNATURES: dict[str, re.Pattern[str]] = {
    "restart_loop": re.compile(
        r"Start request repeated too quickly|start-limit-hit", re.I
    ),
    "undervoltage": re.compile(r"under-?voltage", re.I),
    "out_of_memory": re.compile(
        r"out of memory|oom-kill|MemoryError|Killed process", re.I
    ),
    "storage_io": re.compile(
        r"I/O error|EXT4-fs error|mmc\d.*error|No space left", re.I
    ),
    "timeout": re.compile(r"timed? ?out|TimeoutError", re.I),
    "dns_failure": re.compile(r"Temporary failure in name resolution|gaierror", re.I),
    "model_load_failed": re.compile(
        r"(model|checkpoint).*(failed|cannot load|not found)", re.I
    ),
    "audio_device": re.compile(
        r"no such (audio|sound) device|ALSA.*(error|cannot)", re.I
    ),
}


@dataclass(frozen=True, slots=True)
class LogEntry:
    timestamp: str | None
    level: str | None
    source: str | None
    message: str


def parse_line(line: str) -> LogEntry:
    match = _LINE.match(line.strip())
    if match is None:
        return LogEntry(None, None, None, line.strip())
    return LogEntry(match["ts"], match["level"], match["source"], match["message"])


@dataclass(frozen=True, slots=True)
class LogAnalysis:
    entries: int
    error_count: int
    signatures: dict[str, int]
    first_seen: dict[str, str | None]
    last_seen: dict[str, str | None]
    sample: dict[str, str]


def analyze(lines: list[str], max_lines: int = 5000) -> LogAnalysis:
    counts: Counter[str] = Counter()
    first: dict[str, str | None] = {}
    last: dict[str, str | None] = {}
    sample: dict[str, str] = {}
    errors = 0
    entries = [parse_line(line) for line in lines[-max_lines:] if line.strip()]
    for entry in entries:
        if entry.level in {"ERROR", "CRITICAL"}:
            errors += 1
        for name, pattern in SIGNATURES.items():
            if pattern.search(entry.message):
                counts[name] += 1
                first.setdefault(name, entry.timestamp)
                last[name] = entry.timestamp
                sample.setdefault(name, entry.message[:200])
    return LogAnalysis(len(entries), errors, dict(counts), first, last, sample)

"""Bounded diagnostic history. Persistence is best-effort and never raises."""

from __future__ import annotations

import json
import logging
from collections import deque
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class DiagnosticHistory:
    def __init__(self, path: Path | None = None, limit: int = 200) -> None:
        if limit < 1:
            raise ValueError("history limit must be positive")
        self._path = path
        self._limit = limit
        self._records: deque[dict[str, Any]] = deque(maxlen=limit)
        if path is not None:
            try:
                for line in path.read_text(encoding="utf-8").splitlines()[-limit:]:
                    self._records.append(json.loads(line))
            except (OSError, ValueError):
                self._records.clear()

    def record(self, kind: str, timestamp: float, **data: Any) -> None:
        self._records.append({"kind": kind, "timestamp": timestamp, **data})
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(
                "\n".join(json.dumps(r, separators=(",", ":")) for r in self._records)
                + "\n",
                encoding="utf-8",
            )
            tmp.chmod(0o600)
            tmp.replace(self._path)
        except OSError as error:
            logger.warning("Diagnostic history not persisted: %s", type(error).__name__)

    def recent(self, count: int = 20) -> list[dict[str, Any]]:
        return list(self._records)[-count:]

    def clear(self) -> None:
        self._records.clear()
        if self._path is not None:
            try:
                self._path.unlink(missing_ok=True)
            except OSError:
                logger.warning("Diagnostic history file not removed")

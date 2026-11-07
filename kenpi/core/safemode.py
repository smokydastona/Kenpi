"""One-shot safe mode: start only the loopback status API.

Safe mode skips optional hardware-facing services (vehicle UART link, media
library) so a misbehaving device or corrupted media directory cannot keep the
gateway from coming up. It is requested with ``--safe`` or by creating a marker
file that is consumed on the next start, so a crash loop cannot persist it.
The camera's independent recording is unaffected either way.
"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_MARKER = Path("/var/lib/kenpi/safe-mode-once")


def consume_marker(path: Path = DEFAULT_MARKER) -> bool:
    """Return True and remove the marker if it exists; never raise for absence."""
    try:
        os.unlink(path)
    except FileNotFoundError:
        return False
    return True


def request_next_start(path: Path = DEFAULT_MARKER) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(mode=0o644, exist_ok=True)


def safe_mode_requested(flag: bool, marker: Path = DEFAULT_MARKER) -> bool:
    # Always consume the marker so one request affects exactly one start.
    marked = consume_marker(marker)
    return flag or marked

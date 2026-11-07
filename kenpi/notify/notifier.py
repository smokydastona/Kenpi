"""Forwards selected EventBus events to ntfy and/or a webhook.

Delivery is best-effort and never blocks the bus: events are drained from a bounded queue by
one worker thread, failures are logged and counted, and nothing is invented when a sink is
unreachable. Only transitions that need a human (failures, safe mode, shutdown, controller
loss) are forwarded.
"""

from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from urllib.parse import urlparse

from kenpi.core.events import Event, EventBus, EventType

logger = logging.getLogger(__name__)

NOTIFY_TYPES = frozenset(
    {
        EventType.SERVICE_FAILED,
        EventType.SAFE_MODE_ENTERED,
        EventType.SHUTDOWN_REQUESTED,
        EventType.CONTROLLER_DISCONNECTED,
    }
)
_PRIORITY = {
    EventType.SERVICE_FAILED: "high",
    EventType.SAFE_MODE_ENTERED: "high",
    EventType.SHUTDOWN_REQUESTED: "default",
    EventType.CONTROLLER_DISCONNECTED: "default",
}


def validate_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("notification URL must be http(s) with a host")
    if parsed.username or parsed.password:
        raise ValueError("notification URL must not embed credentials")
    return url


def _post(url: str, body: bytes, headers: dict[str, str], timeout: float) -> None:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        response.read(1024)


class Notifier:
    def __init__(
        self,
        events: EventBus,
        ntfy_url: str = "",
        webhook_url: str = "",
        timeout: float = 5.0,
        post: Callable[[str, bytes, dict[str, str], float], None] = _post,
    ) -> None:
        self._events = events
        self._ntfy = validate_url(ntfy_url) if ntfy_url else ""
        self._webhook = validate_url(webhook_url) if webhook_url else ""
        self._timeout, self._post = timeout, post
        self._queue = None
        self._unsub: Callable[[], None] | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.delivered = 0
        self.failed = 0

    @property
    def active(self) -> bool:
        return bool(self._ntfy or self._webhook)

    def start(self) -> None:
        if not self.active or self._thread:
            return
        self._queue, self._unsub = self._events.subscribe_queue(32, NOTIFY_TYPES)
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="kenpi-notify", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._unsub:
            self._unsub()
            self._unsub = None
        if self._thread:
            self._thread.join(timeout=self._timeout + 2)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            for event in self._queue.drain():
                self.deliver(event)
            self._stop.wait(0.25)

    def deliver(self, event: Event) -> None:
        title = f"Kenpi: {event.type.value}"
        text = f"{event.source} reported {event.type.value}"
        if event.data:
            text += " " + json.dumps(event.data, default=str, sort_keys=True)
        if self._ntfy:
            self._send(
                self._ntfy,
                text.encode(),
                {
                    "Title": title,
                    "Priority": _PRIORITY.get(event.type, "default"),
                    "Tags": "warning",
                },
            )
        if self._webhook:
            self._send(
                self._webhook,
                json.dumps(event.describe()).encode(),
                {"Content-Type": "application/json"},
            )

    def _send(self, url: str, body: bytes, headers: dict[str, str]) -> None:
        try:
            self._post(url, body, headers, self._timeout)
            self.delivered += 1
        except (OSError, urllib.error.URLError, ValueError) as exc:
            self.failed += 1
            logger.warning("Notification to %s failed: %s", urlparse(url).hostname, exc)

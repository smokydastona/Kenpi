"""In-process typed event bus with sequence numbers and a bounded replay ring.

Publishing never blocks and never raises: callback subscribers are isolated from
each other, queue subscribers drop their oldest event when full (and count it),
and the replay ring lets a client that polls ``events_after`` detect gaps from
``oldest_seq``. Only event types that have a real publisher are declared; add a
type together with its producer.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class EventType(str, Enum):
    SERVICE_STARTED = "system.service_started"
    SERVICE_STOPPED = "system.service_stopped"
    SERVICE_FAILED = "system.service_failed"
    SAFE_MODE_ENTERED = "system.safe_mode_entered"
    SHUTDOWN_REQUESTED = "vehicle.shutdown_requested"
    CONTROLLER_CONNECTED = "vehicle.controller_connected"
    CONTROLLER_DISCONNECTED = "vehicle.controller_disconnected"
    POWER_STATE_CHANGED = "vehicle.power_state_changed"
    ASSISTANT_REQUEST = "assistant.request_handled"
    ASSISTANT_DIAGNOSTICS = "assistant.diagnostics_run"


@dataclass(frozen=True, slots=True)
class Event:
    seq: int
    id: str
    timestamp: float
    type: EventType
    source: str
    data: dict[str, object] = field(default_factory=dict)

    def describe(self) -> dict[str, object]:
        return {
            "seq": self.seq,
            "id": self.id,
            "timestamp": self.timestamp,
            "type": self.type.value,
            "source": self.source,
            "data": dict(self.data),
        }


class QueueSubscription:
    """Bounded, drop-oldest event queue for a polling consumer."""

    def __init__(self, maxlen: int, types: frozenset[EventType] | None) -> None:
        if maxlen < 1:
            raise ValueError("queue size must be positive")
        self._items: deque[Event] = deque(maxlen=maxlen)
        self._types = types
        self._lock = threading.Lock()
        self.dropped = 0

    def _offer(self, event: Event) -> None:
        if self._types is not None and event.type not in self._types:
            return
        with self._lock:
            if len(self._items) == self._items.maxlen:
                self.dropped += 1
            self._items.append(event)

    def drain(self) -> list[Event]:
        with self._lock:
            items = list(self._items)
            self._items.clear()
        return items


class EventBus:
    def __init__(
        self,
        replay_size: int = 256,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if replay_size < 1:
            raise ValueError("replay size must be positive")
        self._ring: deque[Event] = deque(maxlen=replay_size)
        self._callbacks: dict[
            int, tuple[Callable[[Event], None], frozenset[EventType] | None]
        ] = {}
        self._queues: dict[int, QueueSubscription] = {}
        self._next_token = 0
        self._seq = 0
        self._closed = False
        self._clock = clock
        self._lock = threading.Lock()
        self.callback_failures = 0
        # Sequence numbers restart with the process; clients compare this to detect it.
        self.stream_id = uuid.uuid4().hex

    def publish(self, type: EventType, source: str, **data: object) -> Event | None:
        with self._lock:
            if self._closed:
                return None
            self._seq += 1
            event = Event(
                self._seq, uuid.uuid4().hex, self._clock(), type, source, data
            )
            self._ring.append(event)
            callbacks = list(self._callbacks.values())
            queues = list(self._queues.values())
        for queue in queues:
            queue._offer(event)
        for callback, types in callbacks:
            if types is not None and event.type not in types:
                continue
            try:
                callback(event)
            except Exception:
                self.callback_failures += 1
                logger.exception("Event subscriber failed for %s", event.type.value)
        return event

    def subscribe(
        self,
        callback: Callable[[Event], None],
        types: frozenset[EventType] | None = None,
    ) -> Callable[[], None]:
        """Register a non-blocking callback; returns an unsubscribe function."""
        with self._lock:
            token = self._next_token = self._next_token + 1
            self._callbacks[token] = (callback, types)

        def unsubscribe() -> None:
            with self._lock:
                self._callbacks.pop(token, None)

        return unsubscribe

    def subscribe_queue(
        self, maxlen: int = 64, types: frozenset[EventType] | None = None
    ) -> tuple[QueueSubscription, Callable[[], None]]:
        subscription = QueueSubscription(maxlen, types)
        with self._lock:
            token = self._next_token = self._next_token + 1
            self._queues[token] = subscription

        def unsubscribe() -> None:
            with self._lock:
                self._queues.pop(token, None)

        return subscription, unsubscribe

    def events_after(self, seq: int = 0, limit: int = 100) -> dict[str, object]:
        """Replay snapshot; ``gap`` is true when events older than the ring were missed."""
        if limit < 1:
            raise ValueError("limit must be positive")
        with self._lock:
            ring = list(self._ring)
            latest = self._seq
        oldest = ring[0].seq if ring else latest + 1
        selected = [e for e in ring if e.seq > seq][:limit]
        return {
            "events": [e.describe() for e in selected],
            "stream_id": self.stream_id,
            "latest_seq": latest,
            "oldest_seq": oldest,
            "gap": bool(ring) and seq < oldest - 1,
        }

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._callbacks.clear()
            self._queues.clear()

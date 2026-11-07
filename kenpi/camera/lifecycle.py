"""Transport-independent camera connection lifecycle state machine."""

from __future__ import annotations

import logging
from collections.abc import Callable
from enum import Enum, auto

logger = logging.getLogger(__name__)


class CameraState(Enum):
    DISCONNECTED = auto()
    DISCOVERING = auto()
    INITIALIZING = auto()
    ACTIVE = auto()
    STREAMING = auto()
    CONNECTION_FAILED = auto()
    AUTHENTICATION_FAILED = auto()
    RECONNECTING = auto()
    SESSION_EXPIRED = auto()
    DEGRADED = auto()
    SHUTTING_DOWN = auto()
    SHUTDOWN = auto()


class CameraLifecycleStateMachine:
    """Track camera lifecycle phases without performing device I/O.

    Failure, expiration, and degradation states require explicit caller-driven
    transitions; entering them never starts a retry or transport operation.
    """

    _ALLOWED_TRANSITIONS = {
        CameraState.DISCONNECTED: frozenset(
            {CameraState.DISCOVERING, CameraState.SHUTTING_DOWN}
        ),
        CameraState.DISCOVERING: frozenset(
            {
                CameraState.INITIALIZING,
                CameraState.CONNECTION_FAILED,
                CameraState.SHUTTING_DOWN,
            }
        ),
        CameraState.INITIALIZING: frozenset(
            {
                CameraState.ACTIVE,
                CameraState.CONNECTION_FAILED,
                CameraState.AUTHENTICATION_FAILED,
                CameraState.SHUTTING_DOWN,
            }
        ),
        CameraState.ACTIVE: frozenset(
            {
                CameraState.STREAMING,
                CameraState.CONNECTION_FAILED,
                CameraState.SESSION_EXPIRED,
                CameraState.DEGRADED,
                CameraState.SHUTTING_DOWN,
            }
        ),
        CameraState.STREAMING: frozenset(
            {
                CameraState.ACTIVE,
                CameraState.CONNECTION_FAILED,
                CameraState.SESSION_EXPIRED,
                CameraState.DEGRADED,
                CameraState.SHUTTING_DOWN,
            }
        ),
        CameraState.CONNECTION_FAILED: frozenset(
            {
                CameraState.DISCONNECTED,
                CameraState.RECONNECTING,
                CameraState.SHUTTING_DOWN,
            }
        ),
        CameraState.AUTHENTICATION_FAILED: frozenset(
            {
                CameraState.DISCONNECTED,
                CameraState.RECONNECTING,
                CameraState.SHUTTING_DOWN,
            }
        ),
        CameraState.RECONNECTING: frozenset(
            {
                CameraState.DISCOVERING,
                CameraState.CONNECTION_FAILED,
                CameraState.AUTHENTICATION_FAILED,
                CameraState.DISCONNECTED,
                CameraState.SHUTTING_DOWN,
            }
        ),
        CameraState.SESSION_EXPIRED: frozenset(
            {
                CameraState.DISCONNECTED,
                CameraState.RECONNECTING,
                CameraState.SHUTTING_DOWN,
            }
        ),
        CameraState.DEGRADED: frozenset(
            {
                CameraState.ACTIVE,
                CameraState.STREAMING,
                CameraState.CONNECTION_FAILED,
                CameraState.RECONNECTING,
                CameraState.SHUTTING_DOWN,
            }
        ),
        CameraState.SHUTTING_DOWN: frozenset({CameraState.SHUTDOWN}),
        CameraState.SHUTDOWN: frozenset(),
    }

    def __init__(
        self,
        on_state_change: Callable[[CameraState, CameraState], None] | None = None,
    ) -> None:
        self._state = CameraState.DISCONNECTED
        self._on_state_change = on_state_change

    @property
    def current_state(self) -> CameraState:
        return self._state

    def transition_to(self, next_state: CameraState, reason: str = "") -> bool:
        """Transition to an allowed phase; any phase can reset to disconnected."""
        if not isinstance(next_state, CameraState):
            logger.warning("Rejected invalid camera lifecycle state: %r", next_state)
            return False
        if next_state is self._state:
            return True
        allowed_transitions = self._ALLOWED_TRANSITIONS[self._state]
        if next_state not in allowed_transitions:
            logger.warning(
                "Rejected invalid camera lifecycle transition: %s -> %s",
                self._state.name,
                next_state.name,
            )
            return False

        old_state = self._state
        self._state = next_state
        logger.info(
            "Camera lifecycle transition: %s -> %s; reason: %s",
            old_state.name,
            next_state.name,
            reason or "unspecified",
        )
        if self._on_state_change is not None:
            try:
                self._on_state_change(old_state, next_state)
            except Exception:
                logger.exception("Camera lifecycle state-change callback failed")
        return True

"""Permission-gated repair actions. Nothing runs without the required approval."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Callable

from kenpi.core.permissions import Permission, validate_permission

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RemediationAction:
    id: str
    description: str
    permission: Permission
    handler: Callable[[], str]
    precondition: Callable[[], str | None] | None = (
        None  # reason it must not run, or None
    )
    verify: Callable[[], str | None] | None = (
        None  # failure reason after running, or None
    )
    rollback: Callable[[], None] | None = None
    timeout: float = 30.0


@dataclass(frozen=True, slots=True)
class RemediationResult:
    action_id: str
    executed: bool
    detail: str
    verified: bool | None = None
    rolled_back: bool = False

    def describe(self) -> dict[str, object]:
        return {
            "action_id": self.action_id,
            "executed": self.executed,
            "detail": self.detail,
            "verified": self.verified,
            "rolled_back": self.rolled_back,
        }


class RemediationRegistry:
    def __init__(self) -> None:
        self._actions: dict[str, RemediationAction] = {}

    def register(self, action: RemediationAction) -> None:
        validate_permission(action.id, action.permission)
        if action.id in self._actions:
            raise ValueError(f"action already registered: {action.id}")
        self._actions[action.id] = action

    def permissions(self) -> dict[str, Permission]:
        return {key: action.permission for key, action in self._actions.items()}

    def describe(self) -> list[dict[str, object]]:
        return [
            {"id": a.id, "description": a.description, "permission": a.permission.name}
            for a in self._actions.values()
        ]

    def execute(self, action_id: str, approved: bool = False) -> RemediationResult:
        action = self._actions.get(action_id)
        if action is None:
            return RemediationResult(action_id, False, "unknown action")
        if (
            action.permission is Permission.NEVER
            or action.permission is Permission.STRICT
        ):
            # STRICT actions need verified camera capability that does not exist yet.
            return RemediationResult(
                action_id, False, "not permitted for the assistant"
            )
        if action.permission >= Permission.ASK and not approved:
            return RemediationResult(
                action_id, False, f"approval required ({action.permission.name})"
            )
        if action.precondition is not None:
            try:
                blocked = action.precondition()
            except Exception as error:
                blocked = f"precondition check failed: {type(error).__name__}"
            if blocked:
                return RemediationResult(action_id, False, f"precondition: {blocked}")
        box: dict[str, object] = {}

        def work() -> None:
            try:
                box["detail"] = action.handler()
            except Exception as error:
                box["error"] = type(error).__name__

        worker = threading.Thread(target=work, daemon=True)
        worker.start()
        worker.join(action.timeout)
        if worker.is_alive():
            # The handler may still be running, so rolling back could race with it.
            logger.warning("Remediation %s timed out", action_id)
            return RemediationResult(action_id, False, "action timed out")
        if "error" in box:
            logger.warning("Remediation %s failed: %s", action_id, box["error"])
            rolled = self._rollback(action)
            return RemediationResult(
                action_id, False, f"action failed: {box['error']}", rolled_back=rolled
            )
        detail = str(box["detail"])
        if action.verify is None:
            return RemediationResult(action_id, True, detail)
        try:
            problem = action.verify()
        except Exception as error:
            problem = f"verification error: {type(error).__name__}"
        if problem:
            rolled = self._rollback(action)
            return RemediationResult(
                action_id,
                False,
                f"verification failed: {problem}",
                verified=False,
                rolled_back=rolled,
            )
        return RemediationResult(action_id, True, detail, verified=True)

    @staticmethod
    def _rollback(action: RemediationAction) -> bool:
        if action.rollback is None:
            return False
        try:
            action.rollback()
            return True
        except Exception as error:
            logger.warning("Rollback of %s failed: %s", action.id, type(error).__name__)
            return False

"""Permission-gated repair actions. Nothing runs without the required approval."""

from __future__ import annotations

import logging
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


@dataclass(frozen=True, slots=True)
class RemediationResult:
    action_id: str
    executed: bool
    detail: str

    def describe(self) -> dict[str, object]:
        return {
            "action_id": self.action_id,
            "executed": self.executed,
            "detail": self.detail,
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
        try:
            return RemediationResult(action_id, True, action.handler())
        except Exception as error:
            logger.warning("Remediation %s failed: %s", action_id, type(error).__name__)
            return RemediationResult(
                action_id, False, f"action failed: {type(error).__name__}"
            )

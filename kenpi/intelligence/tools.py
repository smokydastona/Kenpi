"""Tool registry and policy engine. The planner never executes; the policy decides."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable

from kenpi.core.permissions import Permission, validate_permission

logger = logging.getLogger(__name__)

ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]

_local = threading.local()


def bind_cancel(event: threading.Event | None) -> None:
    """Attach the running tool's cancel event to the current worker thread."""
    _local.event = event


def cancelled() -> bool:
    """True once the running tool was cancelled, timed out or the assistant stopped."""
    event = getattr(_local, "event", None)
    return event is not None and event.is_set()


@dataclass(frozen=True, slots=True)
class Tool:
    name: str
    description: str
    permission: Permission
    handler: ToolHandler
    args: dict[str, type] | None = None  # allowed argument names and types
    gate: Callable[[], str | None] | None = None  # returns a denial reason, or None
    timeout: float = 10.0
    verify: Callable[[dict[str, Any]], str | None] | None = (
        None  # failure reason, or None
    )

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "permission": self.permission.name,
            "args": {k: v.__name__ for k, v in (self.args or {}).items()},
        }


@dataclass(frozen=True, slots=True)
class Decision:
    allowed: bool
    reason: str
    needs_approval: bool = False


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        validate_permission(tool.name, tool.permission)
        if tool.permission is Permission.STRICT and tool.gate is None:
            raise ValueError("strict tools must declare an evidence gate")
        if tool.name in self._tools:
            raise ValueError(f"tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def describe(self) -> list[dict[str, Any]]:
        return [tool.describe() for tool in self._tools.values()]

    def validate_args(self, tool: Tool, args: dict[str, Any]) -> str | None:
        allowed = tool.args or {}
        for key, value in args.items():
            if (
                key not in allowed
                or not isinstance(value, allowed[key])
                or isinstance(value, bool) != (allowed[key] is bool)
            ):
                return f"invalid argument: {key}"
        return None


class PolicyEngine:
    def decide(
        self, tool: Tool, approved: bool = False, user_initiated: bool = True
    ) -> Decision:
        if tool.permission is Permission.NEVER:
            return Decision(False, "this operation is never available to the assistant")
        if not user_initiated and tool.permission > Permission.AUTOMATIC:
            return Decision(False, "this operation requires a user-initiated request")
        if tool.gate is not None:
            reason = tool.gate()
            if reason is not None:
                return Decision(False, reason)
        if tool.permission is Permission.STRICT:
            if not approved:
                return Decision(
                    False, "strict authorization required", needs_approval=True
                )
            return Decision(True, "approved")
        if tool.permission >= Permission.ASK and not approved:
            return Decision(False, "approval required", needs_approval=True)
        return Decision(True, "allowed")

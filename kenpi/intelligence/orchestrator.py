"""Orchestrator: plan -> policy -> execute -> report. The AI never calls devices directly."""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

from kenpi.core.events import EventBus, EventType
from kenpi.intelligence.approvals import ApprovalStore, PendingApproval
from kenpi.intelligence.memory import ConversationStore, SessionStore
from kenpi.intelligence.planner import Plan, Planner
from kenpi.intelligence.tools import PolicyEngine, ToolRegistry
from kenpi.voice.characters import CharacterVoice

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Outcome:
    tool: str
    executed: bool
    reason: str
    result: dict[str, Any] | None = None
    needs_approval: bool = False
    approval_id: str | None = None


@dataclass(frozen=True, slots=True)
class Response:
    message: str
    plan: Plan
    outcomes: tuple[Outcome, ...] = field(default_factory=tuple)

    def describe(self) -> dict[str, Any]:
        return {
            "message": self.message,
            "plan": self.plan.describe(),
            "outcomes": [
                {
                    "tool": o.tool,
                    "executed": o.executed,
                    "reason": o.reason,
                    "needs_approval": o.needs_approval,
                }
                for o in self.outcomes
            ],
        }


class Orchestrator:
    def __init__(
        self,
        registry: ToolRegistry,
        planner: Planner,
        policy: PolicyEngine | None = None,
        sessions: SessionStore | None = None,
        events: EventBus | None = None,
        character: Callable[[], CharacterVoice] | None = None,
        approvals: ApprovalStore | None = None,
        conversations: ConversationStore | None = None,
    ) -> None:
        self.conversations = conversations or ConversationStore()
        self.history: deque[dict[str, Any]] = deque(maxlen=200)
        self.approvals = approvals or ApprovalStore()
        self._character = character
        self._registry = registry
        self._planner = planner
        self._policy = policy or PolicyEngine()
        self._sessions = sessions or SessionStore()
        self._events = events

    def handle(
        self,
        text: str,
        session_id: str = "default",
        approved: bool = False,
        user_initiated: bool = True,
    ) -> Response:
        history = self.conversations.recent(session_id)
        plan = self._planner.plan(text, self._sessions.pending(session_id), history)
        self.conversations.add(session_id, "user", text)
        self._sessions.set_pending(session_id, plan.pending)
        if plan.clarification:
            kind = (
                "unknown_request"
                if plan.confidence == 0.0 and plan.steps == ()
                else None
            )
            if plan.clarification.startswith("I did not catch"):
                kind = None
            return self._finish(
                Response(self._style(kind, plan.clarification), plan), session_id
            )
        outcomes = tuple(
            self._execute(step.tool, step.args, approved, user_initiated, session_id)
            for step in plan.steps
        )
        message = (
            " ".join(
                (o.result or {}).get("message", "") if o.executed else self._refusal(o)
                for o in outcomes
            ).strip()
            or "Nothing to do."
        )
        return self._finish(
            Response(self._style(self._tone(outcomes), message), plan, outcomes),
            session_id,
        )

    @staticmethod
    def _tone(outcomes: tuple[Outcome, ...]) -> str | None:
        """The most significant situation decides the single in-character line."""
        tones = {(o.result or {}).get("tone") for o in outcomes if o.executed}
        for tone in ("diagnostic_fault", "diagnostic_ok"):
            if tone in tones:
                return tone
        refused = [o for o in outcomes if not o.executed]
        if any(o.needs_approval for o in refused):
            return "needs_approval"
        return "refusal" if refused else None

    def _style(self, kind: str | None, message: str) -> str:
        if self._character is None or kind is None:
            return message
        try:
            return self._character().render(kind, message)
        except Exception:
            return message

    def approve(self, approval_id: str) -> Outcome:
        """Run exactly the operation that was requested. Only trusted, local callers (the
        operator CLI or an authenticated on-device UI) may call this; no network route does."""
        pending = self.approvals.consume(approval_id)
        if pending is None:
            return Outcome(
                "unknown", False, "approval is unknown, expired or already used"
            )
        return self._execute(pending.tool, pending.args, True, True, pending.session_id)

    def pending_approvals(self) -> list[PendingApproval]:
        return self.approvals.list()

    def _execute(
        self,
        name: str,
        args: dict[str, Any],
        approved: bool,
        user_initiated: bool,
        session_id: str = "default",
    ) -> Outcome:
        tool = self._registry.get(name)
        if tool is None:
            return Outcome(name, False, "unknown tool")
        invalid = self._registry.validate_args(tool, args)
        if invalid:
            return Outcome(name, False, invalid)
        decision = self._policy.decide(tool, approved, user_initiated)
        if not decision.allowed:
            approval_id = None
            if decision.needs_approval:
                approval_id = self.approvals.request(session_id, name, args).approval_id
            return Outcome(
                name,
                False,
                decision.reason,
                needs_approval=decision.needs_approval,
                approval_id=approval_id,
            )
        started = time.monotonic()
        outcome = self._run(tool, args, decision.reason)
        self.history.append(
            {
                "tool": name,
                "arg_names": sorted(args),
                "session": session_id,
                "executed": outcome.executed,
                "reason": outcome.reason,
                "seconds": round(time.monotonic() - started, 3),
                "at": time.time(),
            }
        )
        return outcome

    @staticmethod
    def _run(tool: Any, args: dict[str, Any], reason: str) -> Outcome:
        """Run a handler in a worker thread so a hung tool cannot block the assistant."""
        box: dict[str, Any] = {}

        def work() -> None:
            try:
                box["result"] = tool.handler(args)
            except Exception as error:
                box["error"] = type(error).__name__

        worker = threading.Thread(target=work, name=f"tool-{tool.name}", daemon=True)
        worker.start()
        worker.join(tool.timeout)
        if worker.is_alive():
            logger.warning("Tool %s timed out after %ss", tool.name, tool.timeout)
            return Outcome(tool.name, False, f"tool timed out after {tool.timeout:g}s")
        if "error" in box:
            logger.warning("Tool %s failed: %s", tool.name, box["error"])
            return Outcome(tool.name, False, f"tool failed: {box['error']}")
        result = box["result"]
        if tool.verify is not None:
            try:
                problem = tool.verify(result)
            except Exception as error:
                problem = f"verification error: {type(error).__name__}"
            if problem:
                return Outcome(tool.name, False, f"verification failed: {problem}")
        return Outcome(tool.name, True, reason, result)

    @staticmethod
    def _refusal(outcome: Outcome) -> str:
        if outcome.needs_approval:
            return f"I need your approval to {outcome.tool.replace('_', ' ')}."
        return f"I can't do that: {outcome.reason}."

    def _finish(self, response: Response, session_id: str = "default") -> Response:
        self.conversations.add(session_id, "assistant", response.message)
        if self._events is not None:
            self._events.publish(
                EventType.ASSISTANT_REQUEST,
                "assistant",
                tools=[o.tool for o in response.outcomes],
                executed=[o.executed for o in response.outcomes],
            )
        return response

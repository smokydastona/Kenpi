"""Intent planning. Produces a plan only; execution is the orchestrator's job."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from kenpi.intelligence.tools import ToolRegistry
from kenpi.voice.characters import resolve_character

CAMERAS = ("front", "rear", "cabin", "interior")
DAYS = ("today", "yesterday")


@dataclass(frozen=True, slots=True)
class PlanStep:
    tool: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Plan:
    steps: tuple[PlanStep, ...]
    confidence: float
    clarification: str | None = None
    pending: dict[str, Any] | None = None  # state to resume once the user answers

    def describe(self) -> dict[str, Any]:
        return {
            "steps": [{"tool": s.tool, "args": dict(s.args)} for s in self.steps],
            "confidence": self.confidence,
            "clarification": self.clarification,
        }


class PlanProvider(Protocol):
    """Optional model-backed planner. Its output is untrusted and always validated."""

    def propose(
        self, text: str, tools: list[dict[str, Any]]
    ) -> list[dict[str, Any]]: ...


_RULES: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern, re.I), tool)
    for pattern, tool in (
        (r"\b(stop|disable|pause|turn off|end)\b.*\brecord", "stop_recording"),
        (
            r"\bset ?up\b|\bget(ting)? started\b|\bfirst time\b|\b(pair|connect)\b.*\b(phone|app)\b",
            "setup_help",
        ),
        (
            r"\b(list|which|what)\b.*\b(characters?|personalit\w+|voices)\b",
            "list_characters",
        ),
        (
            r"\b(switch|change|use|talk|speak|be|let me talk)\b.*\b(brooklyn|gentleman|professor|english|british|southern|fixer|colette|madame|french|default)\b",
            "set_character",
        ),
        (
            r"\b(are you|is it|is the camera|is kenpi)\b.*\brecording\b|\brecording status\b",
            "recording_status",
        ),
        (
            r"\b(storage|space|disk)\b.*\b(left|free|remaining|available)\b|\bhow much (storage|space)\b",
            "storage_status",
        ),
        (
            r"\b(why|what).*\b(slow|wrong|broken|not (working|responding|showing)|isn'?t|won'?t)\b|\bdiagnos|\bhealth\b|\btroubleshoot|\bcheck (the )?system",
            "run_diagnostics",
        ),
        (
            r"\b(open|play|show|browse|list)\b.*\b(recording|clip|video|footage)s?\b",
            "list_recordings",
        ),
        (
            r"\b(show|display|open|switch to)\b.*\b(camera|preview|live)\b",
            "show_camera_preview",
        ),
        (r"\btemperature\b", "system_status"),
        (r"\b(status|how is kenpi|how are you)\b", "system_status"),
        (r"\b(forget|clear|delete)\b.*\b(preferences|memory)\b", "clear_preferences"),
        (
            r"\b(set|change|use)\b.*\b(speed|volume|language|voice|style)\b",
            "set_preference",
        ),
        (r"\b(what can you do|help|tools)\b", "describe_capabilities"),
    )
)

_PREFERENCE_KEYS = {
    "speed": "speech_speed",
    "volume": "volume",
    "language": "language",
    "voice": "voice_profile",
    "style": "response_style",
}


class Planner:
    def __init__(
        self, registry: ToolRegistry, provider: PlanProvider | None = None
    ) -> None:
        self._registry = registry
        self._provider = provider

    def plan(self, text: str, pending: dict[str, Any] | None = None) -> Plan:
        text = text.strip()
        if not text:
            return Plan((), 0.0, "I did not catch anything.")
        if pending is not None:
            resumed = self._resume(text, pending)
            if resumed is not None:
                return resumed
        for pattern, tool in _RULES:
            if pattern.search(text):
                return self._build(tool, text)
        if self._provider is not None:
            steps = self._validated(
                self._provider.propose(text, self._registry.describe())
            )
            if steps:
                return Plan(tuple(steps), 0.5)
        return Plan(
            (),
            0.0,
            "I am not sure what you want. Ask what I can do for the available actions.",
        )

    def _build(self, tool: str, text: str) -> Plan:
        if tool == "list_recordings":
            args: dict[str, Any] = {}
            day = next((d for d in DAYS if d in text.lower()), None)
            camera = next((c for c in CAMERAS if c in text.lower()), None)
            if day:
                args["day"] = day
            if camera:
                args["camera"] = camera
            else:
                return Plan(
                    (),
                    0.7,
                    "Which camera?",
                    {"tool": tool, "args": args, "missing": "camera"},
                )
            return Plan((PlanStep(tool, args),), 0.8)
        if tool == "set_preference":
            lowered = text.lower()
            key = next((v for k, v in _PREFERENCE_KEYS.items() if k in lowered), None)
            match = re.search(r"\bto\s+([\w.\- ]{1,64})$", text.strip(), re.I)
            if key is None or match is None:
                return Plan((), 0.5, "Which setting, and to what value?")
            return Plan(
                (PlanStep(tool, {"key": key, "value": match.group(1).strip()}),), 0.8
            )
        if tool == "set_character":
            character = resolve_character(text)
            if character is None:
                return Plan((), 0.5, "Which character would you like?")
            return Plan((PlanStep(tool, {"character": character.id}),), 0.85)
        if tool == "system_status" and "temperature" in text.lower():
            return Plan((PlanStep(tool, {"topic": "temperature"}),), 0.8)
        return Plan((PlanStep(tool),), 0.85)

    def _resume(self, text: str, pending: dict[str, Any]) -> Plan | None:
        if pending.get("missing") == "camera":
            camera = next((c for c in CAMERAS if c in text.lower()), None)
            if camera:
                args = {**pending["args"], "camera": camera}
                return Plan((PlanStep(pending["tool"], args),), 0.8)
        return None

    def _validated(self, proposals: list[dict[str, Any]]) -> list[PlanStep]:
        """Drop anything that names an unknown tool or has invalid arguments."""
        steps: list[PlanStep] = []
        for item in proposals[:5]:
            if not isinstance(item, dict):
                continue
            tool = self._registry.get(str(item.get("tool", "")))
            args = item.get("args", {})
            if (
                tool is None
                or not isinstance(args, dict)
                or self._registry.validate_args(tool, args)
            ):
                continue
            steps.append(PlanStep(tool.name, args))
        return steps

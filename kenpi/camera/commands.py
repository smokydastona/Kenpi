"""Explicit command registry; unknown operations fail closed."""

from dataclasses import dataclass

from kenpi.core.models import CommandSafety, EvidenceLevel


@dataclass(frozen=True, slots=True)
class CommandDefinition:
    name: str
    safety: CommandSafety
    evidence: EvidenceLevel


class CommandRegistry:
    def __init__(self, definitions: tuple[CommandDefinition, ...] = ()) -> None:
        self._definitions: dict[str, CommandDefinition] = {}
        for definition in definitions:
            if not definition.name.strip():
                raise ValueError("command name is required")
            if definition.name in self._definitions:
                raise ValueError(f"duplicate command: {definition.name}")
            self._definitions[definition.name] = definition

    def classify(self, name: str) -> CommandSafety:
        definition = self._definitions.get(name)
        return definition.safety if definition else CommandSafety.UNKNOWN

    def get(self, name: str) -> CommandDefinition | None:
        return self._definitions.get(name)

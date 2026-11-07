"""Explicit command registry; unknown operations fail closed."""

from dataclasses import dataclass
from typing import Protocol

from kenpi.core.models import CommandSafety, EvidenceLevel


class PayloadValidator(Protocol):
    """Approve an operation's payload with literal True; all other outcomes reject."""

    def __call__(self, payload: bytes) -> bool: ...


@dataclass(frozen=True, slots=True)
class CommandDefinition:
    name: str
    safety: CommandSafety
    evidence: EvidenceLevel
    payload_validator: PayloadValidator | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("command name is required")
        if not isinstance(self.safety, CommandSafety):
            raise ValueError("command safety must be a CommandSafety")
        if not isinstance(self.evidence, EvidenceLevel):
            raise ValueError("command evidence must be an EvidenceLevel")
        if self.payload_validator is not None and not callable(self.payload_validator):
            raise ValueError("payload validator must be callable")


class CommandRegistry:
    def __init__(self, definitions: tuple[CommandDefinition, ...] = ()) -> None:
        self._definitions: dict[str, CommandDefinition] = {}
        for definition in definitions:
            if not isinstance(definition, CommandDefinition):
                raise ValueError("commands must be CommandDefinitions")
            if definition.name in self._definitions:
                raise ValueError(f"duplicate command: {definition.name}")
            self._definitions[definition.name] = definition

    def classify(self, name: str) -> CommandSafety:
        definition = self.get(name)
        return definition.safety if definition else CommandSafety.UNKNOWN

    def get(self, name: str) -> CommandDefinition | None:
        if not isinstance(name, str):
            return None
        return self._definitions.get(name)

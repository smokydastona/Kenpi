"""Errors shared across service boundaries."""


class KenpiError(Exception):
    """Base class for expected service errors."""


class ConfigurationError(KenpiError, ValueError):
    """Configuration is invalid or unsafe."""


class CapabilityUnavailable(KenpiError):
    """An operation is not supported or lacks verified implementation."""


class CommandRejected(KenpiError):
    """A command was rejected by the safety policy."""


class ProtocolError(KenpiError):
    """A message is malformed, unsupported, or inconsistent."""

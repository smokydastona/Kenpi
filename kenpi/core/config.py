"""Small, strict TOML configuration loader with safe local defaults."""

from __future__ import annotations

import ipaddress
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kenpi.core.errors import ConfigurationError


@dataclass(frozen=True, slots=True)
class KenpiConfig:
    environment: str = "production"
    api_host: str = "127.0.0.1"
    api_port: int = 8080
    camera_enabled: bool = False
    camera_request_timeout_seconds: float = 3.0
    vehicle_uart_enabled: bool = False

    def __post_init__(self) -> None:
        if self.environment not in {"production", "development", "test"}:
            raise ConfigurationError("environment must be production, development, or test")
        try:
            ipaddress.ip_address(self.api_host)
        except ValueError as exc:
            raise ConfigurationError("api_host must be an IP address") from exc
        if not 1 <= self.api_port <= 65535:
            raise ConfigurationError("api_port must be between 1 and 65535")
        if not 0.1 <= self.camera_request_timeout_seconds <= 60:
            raise ConfigurationError("camera request timeout must be between 0.1 and 60 seconds")
        if self.environment == "production" and self.api_host not in {"127.0.0.1", "::1"}:
            raise ConfigurationError("production API must bind to loopback until authentication is configured")


def load_config(path: str | Path) -> KenpiConfig:
    """Load only recognized keys from a TOML file; reject unknown configuration."""
    config_path = Path(path)
    try:
        with config_path.open("rb") as stream:
            data: Any = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigurationError(f"cannot load configuration: {exc}") from exc

    if not isinstance(data, dict):
        raise ConfigurationError("configuration root must be a table")
    root = data.get("kenpi", {})
    if not isinstance(root, dict):
        raise ConfigurationError("[kenpi] must be a table")
    allowed = set(KenpiConfig.__dataclass_fields__)
    unknown = set(root) - allowed
    if unknown:
        raise ConfigurationError(f"unknown configuration keys: {', '.join(sorted(unknown))}")
    try:
        return KenpiConfig(**root)
    except TypeError as exc:
        raise ConfigurationError("invalid configuration value") from exc

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
    head_unit: str = "kenwood-dmx5710s"

    def __post_init__(self) -> None:
        from kenpi.headunit.profiles import get_profile

        try:
            get_profile(self.head_unit)
        except ValueError as exc:
            raise ConfigurationError(str(exc)) from exc
        if not isinstance(self.environment, str):
            raise ConfigurationError("environment must be a string")
        if self.environment not in {"production", "development", "test"}:
            raise ConfigurationError(
                "environment must be production, development, or test"
            )
        if not isinstance(self.api_host, str):
            raise ConfigurationError("api_host must be an IP address")
        try:
            address = ipaddress.ip_address(self.api_host)
        except ValueError as exc:
            raise ConfigurationError("api_host must be an IP address") from exc
        if type(self.api_port) is not int or not 1 <= self.api_port <= 65535:
            raise ConfigurationError("api_port must be between 1 and 65535")
        if (
            isinstance(self.camera_request_timeout_seconds, bool)
            or not isinstance(self.camera_request_timeout_seconds, (int, float))
            or not 0.1 <= self.camera_request_timeout_seconds <= 60
        ):
            raise ConfigurationError(
                "camera request timeout must be between 0.1 and 60 seconds"
            )
        if (
            type(self.camera_enabled) is not bool
            or type(self.vehicle_uart_enabled) is not bool
        ):
            raise ConfigurationError("feature flags must be booleans")
        if self.camera_enabled or self.vehicle_uart_enabled:
            raise ConfigurationError(
                "camera and vehicle transports are not implemented"
            )
        if not address.is_loopback:
            raise ConfigurationError(
                "unauthenticated API must bind to loopback in every environment"
            )


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
    unknown_sections = set(data) - {"kenpi"}
    if unknown_sections:
        raise ConfigurationError(
            f"unknown configuration sections: {', '.join(sorted(unknown_sections))}"
        )
    root = data.get("kenpi", {})
    if not isinstance(root, dict):
        raise ConfigurationError("[kenpi] must be a table")
    allowed = set(KenpiConfig.__dataclass_fields__)
    unknown = set(root) - allowed
    if unknown:
        raise ConfigurationError(
            f"unknown configuration keys: {', '.join(sorted(unknown))}"
        )
    try:
        return KenpiConfig(**root)
    except TypeError as exc:
        raise ConfigurationError("invalid configuration value") from exc

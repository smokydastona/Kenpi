"""Read-only Linux platform detection; no hardware control is performed."""

from kenpi.hardware.platform import collect_platform_health

__all__ = ["collect_platform_health"]

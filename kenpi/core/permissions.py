"""Permission levels shared by the diagnostics and intelligence layers."""

from __future__ import annotations

from enum import IntEnum


class Permission(IntEnum):
    AUTOMATIC = 0  # read-only; runs without asking
    ASK = 1  # reversible, non-critical; needs the user's approval
    EXPLICIT = 2  # config, software, firmware, reboot; needs explicit approval
    STRICT = 3  # camera recording behavior; needs approval plus verified capability
    NEVER = 4  # never runnable by the assistant


# Any operation whose id contains one of these words may not be weaker than the minimum.
MINIMUM_BY_KEYWORD: dict[str, Permission] = {
    "stop_record": Permission.STRICT,
    "start_record": Permission.STRICT,
    "set_record": Permission.STRICT,
    "delete_record": Permission.STRICT,
    "enable_record": Permission.STRICT,
    "disable_record": Permission.STRICT,
    "modify_record": Permission.STRICT,
    "camera_command": Permission.STRICT,
    "firmware": Permission.EXPLICIT,
    "reboot": Permission.EXPLICIT,
    "install": Permission.EXPLICIT,
    "uninstall": Permission.EXPLICIT,
    "config": Permission.EXPLICIT,
    "restart": Permission.ASK,
}

# Interrupting recording is never an ordinary assistant action.
NEVER_IDS = frozenset(
    {
        "stop_recording",
        "disable_recording",
        "interrupt_recording",
        "format_camera_storage",
    }
)


def minimum_permission(operation_id: str) -> Permission:
    if operation_id in NEVER_IDS:
        return Permission.NEVER
    minimum = Permission.AUTOMATIC
    for keyword, level in MINIMUM_BY_KEYWORD.items():
        if keyword in operation_id and level > minimum:
            minimum = level
    return minimum


def validate_permission(operation_id: str, permission: Permission) -> None:
    if permission < minimum_permission(operation_id):
        raise ValueError(
            f"{operation_id} requires at least {minimum_permission(operation_id).name} permission"
        )

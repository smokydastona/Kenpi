"""Ranks faults by evidence strength and corroboration. No guessing beyond evidence."""

from __future__ import annotations

from kenpi.diagnostics.models import Cause, Fault, Status

_BASE = {Status.FAIL: 0.75, Status.WARN: 0.5, Status.UNKNOWN: 0.15, Status.OK: 0.0}


def rank(faults: list[Fault]) -> list[Cause]:
    """Independent faults in one subsystem corroborate each other (e.g. a hot reading
    plus matching log lines); each adds 0.1 up to a 0.95 ceiling."""
    causes = []
    for fault in faults:
        peers = sum(
            1
            for other in faults
            if other is not fault
            and other.subsystem is fault.subsystem
            and other.severity is not Status.UNKNOWN
        )
        confidence = min(0.95, _BASE[fault.severity] + 0.1 * peers)
        if fault.severity is Status.UNKNOWN:
            explanation = "No verified evidence exists; this is a limitation, not a detected failure."
        else:
            explanation = f"{len(fault.evidence)} piece(s) of evidence" + (
                f" corroborated by {peers} other finding(s) in {fault.subsystem.value}"
                if peers
                else ""
            )
        causes.append(Cause(fault, confidence, explanation))
    return sorted(causes, key=lambda cause: (-cause.confidence, cause.fault.id))

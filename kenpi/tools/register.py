"""Validate the implementation and validation register against the code."""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

from kenpi.core.capabilities import INITIAL_CAPABILITIES
from kenpi.core.models import CapabilityState, EvidenceLevel

ROOT = Path(__file__).resolve().parents[2]
REGISTER = ROOT / "validation" / "register.toml"
IMPLEMENTATION = frozenset({"complete", "partial", "blocked"})


def load(path: Path = REGISTER) -> list[dict]:
    with path.open("rb") as handle:
        return tomllib.load(handle)["capability"]


def problems(entries: list[dict], root: Path = ROOT) -> list[str]:
    found: list[str] = []
    ids = [e["id"] for e in entries]
    if len(ids) != len(set(ids)):
        found.append("duplicate capability id")
    declared = {c.name: c for c in INITIAL_CAPABILITIES}
    for entry in entries:
        name = entry["id"]
        level = EvidenceLevel[entry["evidence"]]
        if entry["implementation"] not in IMPLEMENTATION:
            found.append(f"{name}: bad implementation status")
        if level >= EvidenceLevel.E4 and not entry.get("evidence_ref"):
            found.append(f"{name}: E4+ requires evidence_ref")
        if entry["implementation"] != "complete" and not entry.get("missing"):
            found.append(f"{name}: incomplete capability must state what is missing")
        for test in entry["tests"]:
            if not (root / test).is_file():
                found.append(f"{name}: missing test file {test}")
        if name in declared:
            cap = declared[name]
            if cap.evidence != level:
                found.append(f"{name}: evidence differs from runtime capability")
            if (
                cap.state is CapabilityState.AVAILABLE
                and entry["implementation"] != "complete"
            ):
                found.append(f"{name}: runtime AVAILABLE but register not complete")
    for name in declared:
        if name not in ids:
            found.append(f"{name}: runtime capability absent from register")
    return found


def main() -> int:
    found = problems(load())
    for line in found:
        print(line, file=sys.stderr)
    print("register ok" if not found else f"{len(found)} problem(s)")
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())

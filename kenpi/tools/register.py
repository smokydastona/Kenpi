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


REQUIRED = {
    "id": str,
    "implementation": str,
    "hardware": bool,
    "evidence": str,
    "tests": list,
    "missing": str,
}


def _schema_problems(entry: object) -> list[str]:
    if not isinstance(entry, dict):
        return ["register entry is not a table"]
    name = entry.get("id", "<unnamed>")
    found = []
    for key, kind in REQUIRED.items():
        if key not in entry:
            found.append(f"{name}: missing field {key}")
        elif not isinstance(entry[key], kind):
            found.append(f"{name}: field {key} must be {kind.__name__}")
    if (
        isinstance(entry.get("evidence"), str)
        and entry["evidence"] not in EvidenceLevel.__members__
    ):
        found.append(f"{name}: unknown evidence level {entry['evidence']!r}")
    if isinstance(entry.get("tests"), list) and not all(
        isinstance(t, str) for t in entry["tests"]
    ):
        found.append(f"{name}: tests must be strings")
    return found


def problems(entries: list[dict], root: Path = ROOT) -> list[str]:
    found: list[str] = []
    for entry in entries:
        found += _schema_problems(entry)
    if found:
        return found
    ids = [e["id"] for e in entries]
    if len(ids) != len(set(ids)):
        found.append("duplicate capability id")
    declared = {c.name: c for c in INITIAL_CAPABILITIES}
    for entry in entries:
        name = entry["id"]
        level = EvidenceLevel[entry["evidence"]]
        if entry["implementation"] not in IMPLEMENTATION:
            found.append(f"{name}: bad implementation status")
        if entry["implementation"] != "complete" and not entry["missing"].strip():
            found.append(f"{name}: incomplete capability must state what is missing")
        if not entry["tests"]:
            found.append(f"{name}: at least one test file is required")
        for test in entry["tests"]:
            if not (root / test).is_file():
                found.append(f"{name}: missing test file {test}")
        if (
            entry["implementation"] == "complete"
            and entry["hardware"]
            and level < EvidenceLevel.E4
        ):
            found.append(f"{name}: complete hardware capability needs E4+ evidence")
        if level >= EvidenceLevel.E4:
            ref = entry.get("evidence_ref")
            if not isinstance(ref, str) or not _inside(root, ref):
                found.append(
                    f"{name}: E4+ requires evidence_ref naming an existing file"
                )
            if not entry["hardware"]:
                found.append(f"{name}: only hardware capabilities carry E4+ evidence")
        if entry["hardware"] and name not in declared:
            found.append(f"{name}: hardware capability is not a runtime capability")
        if name in declared:
            cap = declared[name]
            if not entry["hardware"]:
                found.append(f"{name}: runtime capability must be marked hardware")
            if cap.evidence != level:
                found.append(f"{name}: evidence differs from runtime capability")
            available = cap.state is CapabilityState.AVAILABLE
            if available and entry["implementation"] != "complete":
                found.append(f"{name}: runtime AVAILABLE but register not complete")
            if not available and entry["implementation"] == "complete":
                found.append(f"{name}: register complete but runtime not AVAILABLE")
    for name in declared:
        if name not in ids:
            found.append(f"{name}: runtime capability absent from register")
    return found


def _inside(root: Path, ref: str) -> bool:
    target = (root / ref).resolve()
    return target.is_file() and root.resolve() in target.parents


STATUS_DOC = ROOT / "docs" / "implementation-status.md"
BEGIN, END = "<!-- register:begin -->", "<!-- register:end -->"


def render_table(entries: list[dict]) -> str:
    rows = [
        "| Capability | Implementation | Hardware | Evidence | Missing |",
        "| --- | --- | --- | --- | --- |",
    ]
    for e in entries:
        rows.append(
            f"| {e['id']} | {e['implementation']} | {'yes' if e['hardware'] else 'no'} "
            f"| {e['evidence']} | {e.get('missing', '').strip() or '-'} |"
        )
    return "\n".join(rows)


def status_doc_problems(entries: list[dict], doc: Path = STATUS_DOC) -> list[str]:
    """The generated block in the status doc must equal the register; edit the TOML, not the doc."""
    text = doc.read_text(encoding="utf-8") if doc.is_file() else ""
    if BEGIN not in text or END not in text:
        return [f"{doc.name}: missing register block markers"]
    block = text.split(BEGIN, 1)[1].split(END, 1)[0].strip()
    if block != render_table(entries):
        return [
            f"{doc.name}: register block is stale; run `python -m kenpi.tools.register --write-status`"
        ]
    return []


def write_status(entries: list[dict], doc: Path = STATUS_DOC) -> None:
    text = doc.read_text(encoding="utf-8")
    head, rest = text.split(BEGIN, 1)
    tail = rest.split(END, 1)[1]
    doc.write_text(
        f"{head}{BEGIN}\n{render_table(entries)}\n{END}{tail}", encoding="utf-8"
    )


def main() -> int:
    entries = load()
    if "--write-status" in sys.argv[1:]:
        write_status(entries)
    found = problems(entries)
    if not found:
        found = status_doc_problems(entries)
    for line in found:
        print(line, file=sys.stderr)
    print("register ok" if not found else f"{len(found)} problem(s)")
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())

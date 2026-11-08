"""Integration and dependency-licence audits, derived from the repository itself.

    python -m kenpi.tools.audit [--output validation]

integration-report.json lists Python modules that no entry point can reach
(implemented but disconnected). dependency-licences.json lists the licence
metadata of the installed runtime dependencies and the declared Android and
firmware dependencies. Licence data comes from package metadata only: it is
evidence for a human review, not legal clearance, and an unreadable licence is
reported as "unknown", never assumed permissive.
"""

from __future__ import annotations

import argparse
import ast
import re
import tomllib
from importlib import metadata
from pathlib import Path

from kenpi.tools import register
from kenpi.tools.report import provenance, write

ROOT = register.ROOT
PERMISSIVE = ("mit", "bsd", "apache", "isc", "psf", "python software foundation", "mpl")
COPYLEFT = ("gpl", "agpl", "lgpl", "sspl")


def _module_name(path: Path) -> str:
    parts = list(path.relative_to(ROOT).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _imports(path: Path, known: set[str]) -> set[str]:
    found: set[str] = set()
    package = _module_name(path)
    if path.name != "__init__.py":
        package = package.rpartition(".")[0]
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = package.split(".")
                parts = parts[: len(parts) - node.level + 1]
                base = ".".join(parts + ([base] if base else []))
            names = [base] + [f"{base}.{a.name}" for a in node.names]
        for name in names:
            while name:
                if name in known:
                    found.add(name)
                    break
                name = name.rpartition(".")[0]
            # `from pkg import mod` also loads pkg's submodule mod
            for extra in names:
                if extra in known:
                    found.add(extra)
    return found


def entry_points() -> list[str]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = data.get("project", {}).get("scripts", {})
    return sorted({target.split(":")[0] for target in scripts.values()})


JUSTIFICATIONS = ROOT / "validation" / "integration-justifications.toml"


def load_justifications(path: Path = JUSTIFICATIONS) -> dict[str, str]:
    if not path.is_file():
        return {}
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return {m: str(v.get("reason", "")) for m, v in data.get("module", {}).items()}


def justification_problems(
    unreachable: list[str],
    justifications: dict[str, str],
    complete: set[str],
) -> dict[str, list[str]]:
    """A disconnected module must say why. `blocked:<capability>` is only honest while
    that capability is not complete; once it is, the module must be wired in."""
    unjustified = [m for m in unreachable if not justifications.get(m, "").strip()]
    stale = sorted(m for m in justifications if m not in unreachable)
    invalid = []
    for module in unreachable:
        reason = justifications.get(module, "")
        if reason.startswith("blocked:"):
            capability = reason.split(":", 1)[1].split()[0]
            if capability in complete:
                invalid.append(
                    f"{module}: capability {capability} is complete; wire it in"
                )
    return {"unjustified": unjustified, "stale": stale, "invalid": invalid}


def integration_report() -> dict:
    files = {
        _module_name(p): p
        for p in (ROOT / "kenpi").rglob("*.py")
        if "__pycache__" not in p.parts
    }
    known = set(files)
    graph = {name: _imports(path, known) for name, path in files.items()}
    roots = [e for e in entry_points() if e in known]
    reached: set[str] = set()
    stack = list(roots)
    while stack:
        name = stack.pop()
        if name in reached:
            continue
        reached.add(name)
        # importing a.b.c also initialises its parent packages
        stack.extend(graph.get(name, ()))
        parent = name.rpartition(".")[0]
        if parent in known:
            stack.append(parent)
    unreachable = sorted(
        n for n in known - reached if not n.endswith("__main__") and n != "kenpi"
    )
    justifications = load_justifications()
    complete = {e["id"] for e in register.load() if e["implementation"] == "complete"}
    return {
        **provenance(),
        "justification_problems": justification_problems(
            unreachable, justifications, complete
        ),
        "entry_points": roots,
        "module_count": len(known),
        "reachable_count": len(reached),
        "unreachable_modules": unreachable,
    }


def _classify(text: str) -> str:
    lowered = text.lower()
    if not lowered or lowered == "unknown":
        return "unknown"
    if any(c in lowered for c in COPYLEFT):
        return "copyleft_review"
    if any(p in lowered for p in PERMISSIVE):
        return "permissive"
    return "review"


def _license_of(dist: metadata.Distribution) -> str:
    meta = dist.metadata
    expression = meta.get("License-Expression") or ""
    if expression:
        return expression
    classifiers = [
        c.split("::")[-1].strip()
        for c in meta.get_all("Classifier") or []
        if c.startswith("License ::")
    ]
    if classifiers:
        return "; ".join(classifiers)
    declared = (meta.get("License") or "").strip()
    return declared if 0 < len(declared) < 100 else "unknown"


def python_dependencies() -> list[dict]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    pending = [
        re.split(r"[<>=!~\[; ]", spec, maxsplit=1)[0]
        for spec in data["project"].get("dependencies", [])
    ]
    seen: dict[str, dict] = {}
    while pending:
        name = pending.pop()
        key = name.lower().replace("_", "-")
        if key in seen:
            continue
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            seen[key] = {
                "name": name,
                "version": None,
                "license": "not installed",
                "verdict": "unknown",
            }
            continue
        licence = _license_of(dist)
        seen[key] = {
            "name": dist.metadata["Name"],
            "version": dist.version,
            "license": licence,
            "verdict": _classify(licence),
        }
        for requirement in dist.requires or []:
            if "extra ==" in requirement:
                continue
            pending.append(re.split(r"[<>=!~\[; ]", requirement, maxsplit=1)[0])
    return sorted(seen.values(), key=lambda d: d["name"].lower())


def declared_native_dependencies() -> list[dict]:
    """Android and firmware dependencies named in build files; licences are not audited."""
    found: list[dict] = []
    for path in sorted((ROOT / "android").rglob("*.gradle.kts")):
        text = path.read_text(encoding="utf-8")
        for coords in re.findall(
            r"(?:implementation|api|testImplementation|"
            r'androidTestImplementation)\(\s*"([^"]+)"',
            text,
        ):
            found.append(
                {
                    "source": str(path.relative_to(ROOT)),
                    "dependency": coords,
                    "verdict": "unknown",
                }
            )
    for path in sorted((ROOT / "firmware").glob("platformio.ini")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if (
                "@" in line
                and "=" not in line
                and "/" in line
                or line.strip().startswith("lib_deps")
            ):
                found.append(
                    {
                        "source": str(path.relative_to(ROOT)),
                        "dependency": line.strip(),
                        "verdict": "unknown",
                    }
                )
    return found


def dependency_report() -> dict:
    python = python_dependencies()
    native = declared_native_dependencies()
    return {
        **provenance(),
        "python": python,
        "android_and_firmware_declared": native,
        "needs_review": [d["name"] for d in python if d["verdict"] != "permissive"],
        "note": "Android and firmware licences are not audited here; verdict is unknown.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=ROOT / "validation")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit 1 on copyleft dependencies or unjustified disconnected modules",
    )
    args = parser.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=True)
    integration, dependencies = integration_report(), dependency_report()
    write(args.output / "integration-report.json", integration)
    write(args.output / "dependency-licences.json", dependencies)
    print(
        f"unreachable modules: {len(integration['unreachable_modules'])}; "
        f"dependencies needing review: {len(dependencies['needs_review'])}"
    )
    copyleft = [d for d in dependencies["python"] if d["verdict"] == "copyleft_review"]
    issues = integration["justification_problems"]
    bad = any(issues.values())
    for kind, items in issues.items():
        for item in items:
            print(f"{kind}: {item}")
    return 1 if args.strict and (copyleft or bad) else 0


if __name__ == "__main__":
    raise SystemExit(main())

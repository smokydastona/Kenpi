"""Generate machine-readable validation reports from the real repository state.

    python -m kenpi.tools.report [--output validation] [--skip-tests]

Writes implementation-report.json and hardware-readiness.json from
validation/register.toml (after validating it against the runtime
capabilities) and test-report.json from an actual unittest run. Nothing is
hand-authored: a failing or skipped test is recorded as such.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import unittest
from collections import Counter
from pathlib import Path

from kenpi.tools import register

ROOT = register.ROOT


def _git(*args: str) -> str | None:
    try:
        done = subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def provenance() -> dict:
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "commit": _git("rev-parse", "HEAD"),
        "uncommitted_changes": bool(_git("status", "--porcelain")),
        "python": sys.version.split()[0],
    }


def implementation_report(entries: list[dict], problems: list[str]) -> dict:
    return {
        **provenance(),
        "register_problems": problems,
        "counts": dict(Counter(e["implementation"] for e in entries)),
        "capabilities": [
            {
                "id": e["id"],
                "implementation": e["implementation"],
                "evidence": e["evidence"],
                "hardware": e["hardware"],
                "missing": e["missing"],
            }
            for e in entries
        ],
    }


def hardware_readiness(entries: list[dict]) -> dict:
    """Hardware gates are open until E4+ evidence is recorded for them."""
    items = []
    for e in entries:
        if not e["hardware"]:
            continue
        validated = e["evidence"] in ("E4", "E5") and bool(e.get("evidence_ref"))
        items.append(
            {
                "id": e["id"],
                "status": "validated" if validated else "not_validated",
                "evidence": e["evidence"],
                "evidence_ref": e.get("evidence_ref"),
                "blocking_reason": e["missing"] if not validated else None,
            }
        )
    return {**provenance(), "capabilities": items}


class _Collector(unittest.TextTestResult):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.records: list[dict] = []

    def _add(self, test: unittest.TestCase, outcome: str, detail: str = "") -> None:
        self.records.append({"test": test.id(), "outcome": outcome, "detail": detail})

    def addSuccess(self, test):
        super().addSuccess(test)
        self._add(test, "passed")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._add(test, "failed", self._exc_info_to_string(err, test))

    def addError(self, test, err):
        super().addError(test, err)
        self._add(test, "error", self._exc_info_to_string(err, test))

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self._add(test, "skipped", reason)


def test_report() -> dict:
    suite = unittest.defaultTestLoader.discover(
        str(ROOT / "tests"), top_level_dir=str(ROOT)
    )
    runner = unittest.TextTestRunner(
        stream=open("/dev/null", "w"), resultclass=_Collector, verbosity=0
    )
    started = time.monotonic()
    result = runner.run(suite)
    return {
        **provenance(),
        "duration_seconds": round(time.monotonic() - started, 1),
        "counts": dict(Counter(r["outcome"] for r in result.records)),
        "successful": result.wasSuccessful(),
        "tests": result.records,
    }


def write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=ROOT / "validation")
    parser.add_argument("--skip-tests", action="store_true")
    args = parser.parse_args(argv)
    entries = register.load()
    problems = register.problems(entries)
    args.output.mkdir(parents=True, exist_ok=True)
    write(
        args.output / "implementation-report.json",
        implementation_report(entries, problems),
    )
    write(args.output / "hardware-readiness.json", hardware_readiness(entries))
    ok = not problems
    if not args.skip_tests:
        report = test_report()
        write(args.output / "test-report.json", report)
        ok = ok and report["successful"]
        print(f"tests: {report['counts']}")
    print("reports written to", args.output, "(register problems: %d)" % len(problems))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

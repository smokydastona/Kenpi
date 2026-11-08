import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from kenpi.tools import audit


class AuditTests(unittest.TestCase):
    def test_classification_never_assumes_permissive(self):
        self.assertEqual(audit._classify("MIT"), "permissive")
        self.assertEqual(audit._classify("Apache-2.0 OR BSD-3-Clause"), "permissive")
        self.assertEqual(audit._classify("GPL-3.0-only"), "copyleft_review")
        self.assertEqual(audit._classify("LGPL-2.1"), "copyleft_review")
        self.assertEqual(audit._classify("Custom terms"), "review")
        self.assertEqual(audit._classify("unknown"), "unknown")
        self.assertEqual(audit._classify(""), "unknown")

    def test_integration_report_reaches_main_and_flags_orphans(self):
        report = audit.integration_report()
        self.assertIn("kenpi.main", report["entry_points"])
        self.assertGreater(report["reachable_count"], 20)
        self.assertNotIn("kenpi.main", report["unreachable_modules"])
        self.assertNotIn("kenpi.assistant", report["unreachable_modules"])

    def test_dependency_report_lists_runtime_dependencies(self):
        names = {d["name"].lower() for d in audit.python_dependencies()}
        self.assertIn("cryptography", names)
        for entry in audit.python_dependencies():
            self.assertIn(
                entry["verdict"],
                {"permissive", "copyleft_review", "review", "unknown"},
            )

    def test_cli_writes_reports_and_strict_flags_copyleft(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(audit.main(["--output", d]), 0)
            for name in ("integration-report.json", "dependency-licences.json"):
                data = json.loads((Path(d) / name).read_text())
                self.assertIn("commit", data)

    def test_repository_has_no_unjustified_modules(self):
        issues = audit.integration_report()["justification_problems"]
        self.assertEqual(issues, {"unjustified": [], "stale": [], "invalid": []})

    def test_justification_rules(self):
        found = audit.justification_problems(
            ["a", "b", "c"],
            {"a": "ok", "b": "blocked:camera_control x", "gone": "old"},
            {"camera_control"},
        )
        self.assertEqual(found["unjustified"], ["c"])
        self.assertEqual(found["stale"], ["gone"])
        self.assertEqual(len(found["invalid"]), 1)

    def test_strict_fails_on_unjustified(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(audit, "load_justifications", return_value={}):
                self.assertEqual(audit.main(["--output", d, "--strict"]), 1)


if __name__ == "__main__":
    unittest.main()

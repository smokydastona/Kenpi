import json
import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()

import json
import tempfile
import unittest
from pathlib import Path

from kenpi.tools import register, report


class ReportTests(unittest.TestCase):
    def test_reports_reflect_register_and_never_claim_hardware(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            code = report.main(["--output", d, "--skip-tests"])
            impl = json.loads((Path(d) / "implementation-report.json").read_text())
            hw = json.loads((Path(d) / "hardware-readiness.json").read_text())
        self.assertEqual(code, 0)
        self.assertEqual(impl["register_problems"], [])
        self.assertEqual(len(impl["capabilities"]), len(register.load()))
        self.assertTrue(hw["capabilities"])
        for item in hw["capabilities"]:
            if item["status"] == "not_validated":
                self.assertTrue(item["blocking_reason"])

    def test_unvalidated_without_evidence_ref(self) -> None:
        entry = {
            "id": "x",
            "implementation": "complete",
            "hardware": True,
            "evidence": "E4",
            "tests": ["t"],
            "missing": "",
        }
        self.assertEqual(
            report.hardware_readiness([entry])["capabilities"][0]["status"],
            "not_validated",
        )

    def test_collector_records_failures_and_skips(self) -> None:
        class Sample(unittest.TestCase):
            def test_ok(self) -> None:
                pass

            def test_bad(self) -> None:
                self.fail("boom")

            @unittest.skip("why")
            def test_skip(self) -> None:
                pass

        runner = unittest.TextTestRunner(
            stream=open("/dev/null", "w"), resultclass=report._Collector
        )
        result = runner.run(unittest.defaultTestLoader.loadTestsFromTestCase(Sample))
        outcomes = {r["test"].split(".")[-1]: r["outcome"] for r in result.records}
        self.assertEqual(
            outcomes,
            {"test_ok": "passed", "test_bad": "failed", "test_skip": "skipped"},
        )

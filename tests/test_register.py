import unittest

from kenpi.tools import register


class RegisterTests(unittest.TestCase):
    def test_repository_register_is_consistent(self):
        self.assertEqual(register.problems(register.load()), [])

    def test_hardware_evidence_cannot_be_promoted_without_reference(self):
        entries = [
            {
                "id": "x",
                "implementation": "complete",
                "evidence": "E4",
                "hardware": True,
                "tests": [],
                "missing": "",
            }
        ]
        self.assertTrue(any("evidence_ref" in p for p in register.problems(entries)))

    def test_incomplete_must_state_gap_and_tests_must_exist(self):
        entries = [
            {
                "id": "y",
                "implementation": "partial",
                "evidence": "E0",
                "hardware": False,
                "tests": ["tests/nope.py"],
                "missing": "",
            }
        ]
        found = register.problems(entries)
        self.assertTrue(any("what is missing" in p for p in found))
        self.assertTrue(any("missing test file" in p for p in found))

    def entry(self, **over):
        base = {
            "id": "head_unit_registry",
            "implementation": "complete",
            "hardware": False,
            "evidence": "E0",
            "tests": ["tests/test_headunit.py"],
            "missing": "",
        }
        base.update(over)
        return base

    def test_schema_errors_are_reported_not_raised(self):
        self.assertTrue(register.problems([{"id": "z"}]))
        self.assertTrue(
            any(
                "unknown evidence" in p
                for p in register.problems([self.entry(evidence="E9")])
            )
        )
        self.assertTrue(register.problems(["bad"]))

    def test_unknown_hardware_id_rejected(self):
        found = register.problems([self.entry(id="invented", hardware=True)])
        self.assertTrue(any("not a runtime capability" in p for p in found))

    def test_evidence_ref_must_exist(self):
        bad = self.entry(
            hardware=True, evidence="E4", evidence_ref="captures/nope.json"
        )
        self.assertTrue(any("existing file" in p for p in register.problems([bad])))
        self.assertTrue(
            any(
                "only hardware" in p
                for p in register.problems(
                    [self.entry(evidence="E4", evidence_ref="README.md")]
                )
            )
        )

    def test_complete_runtime_blocked_mismatch(self):
        found = register.problems([self.entry(id="camera_control", hardware=True)])
        self.assertTrue(any("not AVAILABLE" in p for p in found))

    def test_complete_hardware_needs_e4(self):
        found = register.problems(
            [self.entry(id="camera_control", hardware=True, implementation="complete")]
        )
        self.assertTrue(any("needs E4+" in p for p in found))

    def test_status_doc_matches_register(self):
        self.assertEqual(register.status_doc_problems(register.load()), [])

    def test_stale_status_doc_detected_and_rewritten(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as d:
            doc = Path(d) / "s.md"
            doc.write_text(f"x\n{register.BEGIN}\nold\n{register.END}\ny")
            entries = register.load()
            self.assertTrue(register.status_doc_problems(entries, doc))
            register.write_status(entries, doc)
            self.assertEqual(register.status_doc_problems(entries, doc), [])
            self.assertTrue(doc.read_text().endswith("y"))


if __name__ == "__main__":
    unittest.main()

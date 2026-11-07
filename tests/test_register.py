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
                "tests": ["tests/nope.py"],
                "missing": "",
            }
        ]
        found = register.problems(entries)
        self.assertTrue(any("what is missing" in p for p in found))
        self.assertTrue(any("missing test file" in p for p in found))


if __name__ == "__main__":
    unittest.main()

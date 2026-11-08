import json
import tempfile
import unittest
from pathlib import Path

from kenpi.tools.capture_summary import load_har, main, render, summarise


def har(*urls, extra=""):
    entries = [
        {
            "request": {"method": m, "url": u},
            "response": {
                "status": s,
                "content": {"mimeType": "application/json; charset=x", "size": 12},
            },
        }
        for m, u, s in urls
    ]
    return json.dumps({"log": {"entries": entries}}) + extra


class CaptureSummaryTests(unittest.TestCase):
    def write(self, text):
        d = tempfile.mkdtemp()
        p = Path(d) / "c.har"
        p.write_text(text)
        return p

    def test_groups_and_never_claims_verification(self):
        p = self.write(
            har(
                ("GET", "http://example.test:8080/api/a?x=1&y=2", 200),
                ("GET", "http://example.test:8080/api/a?x=3", 200),
                ("POST", "http://example.test:8080/api/b", 500),
            )
        )
        rows = summarise(load_har(p))
        self.assertEqual(len(rows), 2)
        first = rows[0]
        self.assertEqual(
            (first["count"], first["query_keys"], first["port"]), (2, ["x", "y"], 8080)
        )
        text = render(rows, "A810 Lite", "v1")
        self.assertIn("unverified", text)
        self.assertIn("not command definitions", text)

    def test_unredacted_capture_is_refused(self):
        p = self.write(har(("GET", "http://example.test/api?token=abcdef123456", 200)))
        with self.assertRaises(ValueError):
            load_har(p)

    def test_redacted_values_accepted(self):
        p = self.write(har(("GET", "http://example.test/api?token=[REDACTED]", 200)))
        self.assertEqual(len(load_har(p)), 1)

    def test_malformed_inputs_rejected(self):
        for text in ("not json", "[]", '{"log": {"entries": 5}}'):
            with self.assertRaises(ValueError):
                load_har(self.write(text))

    def test_cli_error_exit(self):
        self.assertEqual(
            main([str(self.write("x")), "--model", "m", "--firmware", "f"]), 1
        )


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from pathlib import Path

from kenpi.tools.redact import REDACTED, redact_file, redact_text


class RedactTests(unittest.TestCase):
    def assert_removed(self, text: str, *secrets: str) -> str:
        redacted, counts = redact_text(text)
        for secret in secrets:
            self.assertNotIn(secret, redacted)
        self.assertTrue(counts)
        return redacted

    def test_headers_and_bearer(self) -> None:
        self.assert_removed(
            "GET /x HTTP/1.1\nAuthorization: Bearer abcdef123456\n"
            "Cookie: sid=zzz999\nX-Device-Token: t0k3n",
            "abcdef123456",
            "zzz999",
            "t0k3n",
        )

    def test_key_values_json_and_query(self) -> None:
        self.assert_removed(
            'GET /cmd?token=abc123&ssid=HomeNet&mode=1\n{"password": "hunter2"}',
            "abc123",
            "HomeNet",
            "hunter2",
        )

    def test_mac_gps_and_nmea(self) -> None:
        self.assert_removed(
            "dev aa:bb:cc:dd:ee:ff at 37.774929, -122.419416\n"
            "$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,,*6A",
            "aa:bb:cc:dd:ee:ff",
            "37.774929",
            "4807.038",
        )

    def test_benign_text_unchanged(self) -> None:
        text = "GET /status HTTP/1.1\nHost: 192.168.0.1\nmode=1 version 1.2.3"
        redacted, counts = redact_text(text)
        self.assertEqual(redacted, text)
        self.assertFalse(counts)

    def test_marker_present(self) -> None:
        redacted = self.assert_removed("password=abc")
        self.assertIn(REDACTED, redacted)

    def test_file_rules(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            source = root / "in.txt"
            source.write_text("token=abc", encoding="utf-8")
            with self.assertRaises(ValueError):
                redact_file(source, source)
            binary = root / "in.pcap"
            binary.write_bytes(b"\xd4\xc3\x00\x00")
            with self.assertRaises(ValueError):
                redact_file(binary, root / "out")
            out = root / "out.txt"
            self.assertEqual(redact_file(source, out)["key_value"], 1)
            self.assertNotIn("abc", out.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from pathlib import Path

from kenpi.core import safemode


class SafeModeTests(unittest.TestCase):
    def test_marker_is_one_shot(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "state" / "safe-once"
            self.assertFalse(safemode.safe_mode_requested(False, marker))
            safemode.request_next_start(marker)
            self.assertTrue(safemode.safe_mode_requested(False, marker))
            self.assertFalse(marker.exists())
            self.assertFalse(safemode.safe_mode_requested(False, marker))

    def test_flag_still_consumes_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "m"
            safemode.request_next_start(marker)
            self.assertTrue(safemode.safe_mode_requested(True, marker))
            self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kenpi.tools import video_bench


class VideoBenchTests(unittest.TestCase):
    def test_pipeline_accounts_for_every_frame_and_bounds_memory(self):
        result = asyncio.run(video_bench.bench_pipeline(300, 64, 48, 4, 0.2))
        self.assertTrue(result["accounting_ok"])
        self.assertGreater(result["frames_dropped"], 0)
        self.assertLess(result["peak_python_bytes"], result["frame_bytes"] * 40)

    def test_rejects_bad_parameters(self):
        for args in ((0, 64, 48, 4, 0), (10, 8, 48, 4, 0), (10, 64, 48, 4, -1)):
            with self.assertRaises(ValueError):
                asyncio.run(video_bench.bench_pipeline(*args))

    def test_decode_unavailable_without_ffmpeg(self):
        with mock.patch("shutil.which", return_value=None):
            self.assertEqual(
                video_bench.bench_decode(Path("x"))["status"], "unavailable"
            )

    def test_decode_missing_file(self):
        with mock.patch("shutil.which", return_value="/usr/bin/ffmpeg"):
            with self.assertRaises(ValueError):
                video_bench.bench_decode(Path(tempfile.gettempdir()) / "nope.mp4")

    def test_cli_runs(self):
        self.assertEqual(
            video_bench.main(
                ["pipeline", "--frames", "50", "--width", "64", "--height", "48"]
            ),
            0,
        )


if __name__ == "__main__":
    unittest.main()

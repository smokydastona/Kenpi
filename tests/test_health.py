import tempfile
import unittest
from pathlib import Path

from kenpi.services.health import collect_host_health


class HealthTests(unittest.TestCase):
    def test_collects_present_metrics_and_leaves_missing_temperature_null(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            meminfo = root / "meminfo"
            meminfo.write_text(
                "MemTotal:       2048 kB\nMemAvailable:    1024 kB\n",
                encoding="ascii",
            )
            health = collect_host_health(
                meminfo_path=meminfo,
                cpu_temperature_path=root / "missing-temp",
                storage_path=root,
            )
        self.assertEqual(health["memory_total_bytes"], 2 * 1024 * 1024)
        self.assertEqual(health["memory_available_bytes"], 1024 * 1024)
        self.assertIsNone(health["cpu_temperature_c"])
        self.assertIsNotNone(health["storage_free_bytes"])


if __name__ == "__main__":
    unittest.main()

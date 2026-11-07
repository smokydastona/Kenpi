import unittest
from unittest.mock import patch

from kenpi.services.health import collect_host_health
from tests.test_hardware import VirtualFilesystem


class HealthTests(unittest.TestCase):
    def test_collects_present_metrics_and_leaves_missing_temperature_null(self) -> None:
        with VirtualFilesystem() as filesystem:
            root = filesystem.root
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
        self.assertEqual(
            health["hardware"]["capabilities"]["cpu_temperature"], "unavailable"
        )

    def test_hardware_telemetry_is_used_by_host_health(self) -> None:
        with VirtualFilesystem() as filesystem:
            root = filesystem.root
            tree = root / "tree"
            tree.mkdir()
            (tree / "compatible").write_bytes(b"raspberrypi,5-model-b\0")
            zone = root / "thermal_zone1"
            zone.mkdir()
            (zone / "type").write_text("cpu-thermal")
            (zone / "temp").write_text("51000")
            uptime = root / "uptime"
            uptime.write_text("21.5 10.0")
            health = collect_host_health(
                meminfo_path=root / "missing-meminfo",
                storage_path=root / "missing-storage",
                device_tree_paths=(tree,),
                thermal_root=root,
                uptime_path=uptime,
            )
        self.assertEqual(health["hardware"]["platform"]["board"], "pi5")
        self.assertEqual(health["cpu_temperature_c"], 51.0)
        self.assertEqual(health["hardware"]["uptime_seconds"], 21.5)
        self.assertIsNone(health["memory_total_bytes"])
        self.assertIsNone(health["storage_total_bytes"])

    def test_invalid_memory_units_and_negative_values_are_not_reported(self) -> None:
        with VirtualFilesystem() as filesystem:
            root = filesystem.root
            meminfo = root / "meminfo"
            meminfo.write_text("MemTotal: -10 kB\nMemAvailable: 20 unknown\n")
            health = collect_host_health(meminfo_path=meminfo)
        self.assertIsNone(health["memory_total_bytes"])
        self.assertIsNone(health["memory_available_bytes"])

    def test_explicit_temperature_path_keeps_telemetry_consistent(self) -> None:
        with VirtualFilesystem() as filesystem:
            root = filesystem.root
            temperature = root / "temperature"
            temperature.write_text("35000")
            health = collect_host_health(cpu_temperature_path=temperature)
        self.assertEqual(health["cpu_temperature_c"], 35.0)
        self.assertEqual(health["hardware"]["cpu_temperature_c"], 35.0)
        self.assertEqual(
            health["hardware"]["capabilities"]["cpu_temperature"], "available"
        )
        self.assertEqual(health["hardware"]["cpu_temperature_sensor"], "configured")

    def test_failed_reads_expose_sanitized_diagnostics(self) -> None:
        with (
            patch(
                "pathlib.Path.read_text",
                side_effect=PermissionError("private/path and secret exception detail"),
            ),
            patch(
                "kenpi.services.health.shutil.disk_usage",
                side_effect=OSError("private/path and secret exception detail"),
            ),
        ):
            health = collect_host_health()
        self.assertEqual(health["collection_status"], "partial")
        self.assertIn(
            {"metric": "memory", "reason": "permission_denied"},
            health["collection_errors"],
        )
        self.assertIn(
            {"metric": "storage", "reason": "read_failed"},
            health["collection_errors"],
        )
        self.assertNotIn("private/path", str(health))
        self.assertNotIn("secret exception detail", str(health))

    def test_valid_metric_survives_other_invalid_memory_metric(self) -> None:
        with VirtualFilesystem() as filesystem:
            root = filesystem.root
            meminfo = root / "meminfo"
            meminfo.write_text("MemTotal: 20 kB\nMemAvailable: malformed\n")
            health = collect_host_health(meminfo_path=meminfo)
        self.assertEqual(health["memory_total_bytes"], 20 * 1024)
        self.assertIsNone(health["memory_available_bytes"])
        self.assertIn(
            {"metric": "memory_available_bytes", "reason": "invalid_reading"},
            health["collection_errors"],
        )

    def test_complete_health_has_empty_diagnostics(self) -> None:
        with VirtualFilesystem() as filesystem:
            root = filesystem.root
            tree = root / "tree"
            tree.mkdir()
            (tree / "model").write_bytes(b"Raspberry Pi 5 Model B\0")
            (tree / "compatible").write_bytes(b"raspberrypi,5-model-b\0")
            uptime = root / "uptime"
            uptime.write_text("12.5 0")
            meminfo = root / "meminfo"
            meminfo.write_text("MemTotal: 2048 kB\nMemAvailable: 1024 kB\n")
            temperature = root / "temperature"
            temperature.write_text("42500")
            network = root / "net"
            network.mkdir()
            health = collect_host_health(
                meminfo_path=meminfo,
                storage_path=root,
                device_tree_paths=(tree,),
                uptime_path=uptime,
                cpu_temperature_path=temperature,
                network_root=network,
            )
        self.assertEqual(health["collection_status"], "complete")
        self.assertEqual(health["collection_errors"], [])
        self.assertEqual(health["hardware"]["collection_errors"], [])
        self.assertTrue(
            {
                "application_status",
                "cpu_temperature_c",
                "memory_total_bytes",
                "memory_available_bytes",
                "storage_total_bytes",
                "storage_free_bytes",
            }
            <= health.keys()
        )

    def test_missing_memory_field_and_invalid_storage_source_are_explicit(self) -> None:
        with VirtualFilesystem() as filesystem:
            root = filesystem.root
            meminfo = root / "meminfo"
            meminfo.write_text("MemTotal: 10 kB\n")
            with patch(
                "kenpi.services.health.shutil.disk_usage",
                side_effect=ValueError("private source details"),
            ):
                health = collect_host_health(meminfo_path=meminfo)
        self.assertIn(
            {"metric": "memory_available_bytes", "reason": "missing_value"},
            health["collection_errors"],
        )
        self.assertIn(
            {"metric": "storage", "reason": "invalid_source"},
            health["collection_errors"],
        )
        self.assertNotIn("private source details", str(health))


if __name__ == "__main__":
    unittest.main()

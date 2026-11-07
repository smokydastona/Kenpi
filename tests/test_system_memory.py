import tempfile
import unittest
from pathlib import Path

from kenpi.diagnostics.checks import check_system
from kenpi.diagnostics.models import DiagnosticContext, Status
from kenpi.diagnostics.system import collect_system
from kenpi.intelligence.memory import ConversationStore


class SystemTests(unittest.TestCase):
    def test_reads_real_files_and_unknown_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "loadavg").write_text("3.00 1 1 1/1 1")
            (root / "meminfo").write_text("SwapTotal: 1000 kB\nSwapFree: 200 kB\n")
            fw = root / "sys/devices/platform/soc/soc:firmware"
            fw.mkdir(parents=True)
            (fw / "get_throttled").write_text("50005\n")
            data = collect_system(root, root / "sys", ("false",), cpu_count=2)
            empty = collect_system(root / "none", root / "none", ("false",), 2)
        self.assertEqual(data["load_1m"], 3.0)
        self.assertEqual(data["swap_used_bytes"], 800 * 1024)
        self.assertEqual(data["throttled_flags"], 0x50005)
        self.assertIsNone(empty["throttled_flags"])
        self.assertIsNone(empty["load_1m"])

    def test_checks_flag_undervoltage_and_report_unknown(self) -> None:
        def run(data):
            ctx = DiagnosticContext(host_health=lambda: {}, system=lambda: data)
            return {r.name: r for r in check_system(ctx)}

        out = run(
            {
                "load_1m": 8.0,
                "cpu_count": 4,
                "throttled_flags": 0x50005,
                "swap_total_bytes": 100,
                "swap_used_bytes": 90,
                "usb_devices": ["1-1"],
            }
        )
        self.assertEqual(out["throttling"].status, Status.FAIL)
        self.assertEqual(out["cpu_load"].status, Status.WARN)
        self.assertEqual(out["swap"].status, Status.WARN)
        out = run({})
        self.assertEqual(out["throttling"].status, Status.UNKNOWN)
        self.assertEqual(out["usb"].status, Status.UNKNOWN)


class ConversationTests(unittest.TestCase):
    def test_persist_redact_isolate_expire_delete(self) -> None:
        now = [1000.0]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "c.json"
            store = ConversationStore(path, 100, lambda: now[0])
            store.add("a", "user", "token=" + "x" * 40 + " hello")
            store.add("b", "user", "other")
            self.assertEqual(oct(path.stat().st_mode & 0o777), "0o600")
            self.assertNotIn("x" * 40, path.read_text())
            again = ConversationStore(path, 100, lambda: now[0])
            self.assertEqual(len(again.recent("a")), 1)
            self.assertEqual(again.recent("c"), [])
            now[0] += 101
            self.assertEqual(again.recent("a"), [])
            again.add("a", "user", "new")
            again.delete("a")
            self.assertEqual(again.export("a"), [])
            again.delete()
            self.assertFalse(path.exists())

    def test_corrupt_file_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "c.json"
            path.write_text("{bad")
            self.assertEqual(ConversationStore(path).recent("a"), [])


if __name__ == "__main__":
    unittest.main()

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


class NetworkTests(unittest.TestCase):
    def test_collects_route_dns_wireguard_and_cert(self) -> None:
        from datetime import datetime, timedelta, timezone

        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID

        from kenpi.diagnostics.checks import check_network_routes
        from kenpi.diagnostics.system import collect_network

        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "t")])
        start = datetime.now(timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(1)
            .not_valid_before(start - timedelta(days=1))
            .not_valid_after(start + timedelta(days=10))
            .sign(key, hashes.SHA256())
        )
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "net").mkdir()
            (root / "net/route").write_text(
                "Iface\tDestination\tGateway\neth0\t00000000\t0100A8C0\n"
            )
            (root / "resolv.conf").write_text("nameserver 192.168.0.1\n")
            wg = root / "sys/class/net/wg0"
            wg.mkdir(parents=True)
            (wg / "uevent").write_text("DEVTYPE=wireguard\n")
            pem = root / "gw.pem"
            pem.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
            data = collect_network(root, root / "sys", root / "resolv.conf", pem)
            missing = collect_network(root / "x", root / "x", root / "x", None)
        self.assertEqual(data["default_gateway"], "192.168.0.1")
        self.assertEqual(data["dns_servers"], ["192.168.0.1"])
        self.assertEqual(data["wireguard_interfaces"], ["wg0"])
        self.assertAlmostEqual(data["cert_days_left"], 10, delta=0.1)
        ctx = DiagnosticContext(host_health=lambda: {}, network=lambda: data)
        out = {r.name: r for r in check_network_routes(ctx)}
        self.assertEqual(out["tls_certificate"].status, Status.WARN)
        self.assertEqual(out["routing"].status, Status.OK)
        self.assertIsNone(missing["has_default_route"])
        ctx = DiagnosticContext(host_health=lambda: {}, network=lambda: missing)
        self.assertTrue(
            all(r.status == Status.UNKNOWN for r in check_network_routes(ctx))
        )


class ListenCommandTests(unittest.TestCase):
    def test_listen_reports_unavailable_without_audio_tools(self) -> None:
        import contextlib
        import io

        from kenpi.assistant_cli import main
        from kenpi.features import FeatureStore

        with tempfile.TemporaryDirectory() as d:
            FeatureStore(Path(d)).set_enabled("assistant", True)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                code = main(["--state-directory", d, "listen"])
        self.assertEqual(code, 1)
        self.assertIn("unavailable", err.getvalue())

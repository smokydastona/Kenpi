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


class NetworkTests(unittest.TestCase):
    @staticmethod
    def _no_answer(_name):
        raise OSError("resolver unavailable")

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
            data = collect_network(
                root, root / "sys", root / "resolv.conf", pem, resolver=lambda n: "x"
            )
            missing = collect_network(
                root / "x", root / "x", root / "x", None, resolver=self._no_answer
            )
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


class FilesystemTests(unittest.TestCase):
    def test_readonly_mounts_and_check(self) -> None:
        from kenpi.diagnostics.checks import DiagnosticContext, check_system
        from kenpi.diagnostics.models import Status
        from kenpi.diagnostics.system import readonly_mounts

        text = (
            "/dev/mmcblk0p2 / ext4 ro,relatime 0 0\n"
            "/dev/mmcblk0p1 /boot/firmware vfat rw,relatime 0 0\n"
            "proc /proc proc ro 0 0\n"
        )
        self.assertEqual(readonly_mounts(text), ["/"])
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "mounts").write_text(text)
            data = collect_system(root, root / "sys", ("false",), 2)
        self.assertEqual(data["readonly_mounts"], ["/"])
        results = check_system(DiagnosticContext(system=lambda: data))
        fs = next(r for r in results if r.name == "filesystem")
        self.assertEqual(fs.status, Status.FAIL)
        with tempfile.TemporaryDirectory() as d:
            data = collect_system(Path(d), Path(d), ("false",), 2)
        fs = next(
            r
            for r in check_system(DiagnosticContext(system=lambda: data))
            if r.name == "filesystem"
        )
        self.assertEqual(fs.status, Status.UNKNOWN)


class WireGuardHandshakeTests(unittest.TestCase):
    def test_handshake_age_from_real_subprocess(self) -> None:
        import stat

        from kenpi.diagnostics.checks import DiagnosticContext, check_network_routes
        from kenpi.diagnostics.models import Status
        from kenpi.diagnostics.system import collect_network

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            net = root / "sys/class/net/wg0"
            net.mkdir(parents=True)
            (net / "uevent").write_text("DEVTYPE=wireguard\n")
            fake = root / "wg"
            fake.write_text('#!/bin/sh\nprintf "PEERKEY\\t%s\\n" "$KENPI_TEST_STAMP"\n')
            fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
            import os

            def run(stamp: str):
                os.environ["KENPI_TEST_STAMP"] = stamp
                data = collect_network(
                    root / "proc",
                    root / "sys",
                    root / "none",
                    None,
                    1000.0,
                    (str(fake),),
                )
                checks = check_network_routes(DiagnosticContext(network=lambda: data))
                return data, next(c for c in checks if c.name == "wireguard_peer")

            try:
                data, fresh = run("950")
                self.assertEqual(data["wireguard_handshake_age"], {"wg0": 50.0})
                self.assertEqual(fresh.status, Status.OK)
                self.assertNotIn("PEERKEY", repr(data))
                _, never = run("0")
                self.assertEqual(never.status, Status.WARN)
                _, old = run("100")
                self.assertEqual(old.status, Status.WARN)
            finally:
                os.environ.pop("KENPI_TEST_STAMP", None)
            data = collect_network(
                root / "proc",
                root / "sys",
                root / "none",
                None,
                1000.0,
                ("no-such-wg",),
            )
            self.assertIsNone(data["wireguard_handshake_age"])


class HostnameResolutionTests(unittest.TestCase):
    def test_resolves_fails_and_hangs(self):
        import socket
        import threading

        from kenpi.diagnostics.system import _hostname_resolves

        self.assertTrue(_hostname_resolves(lambda n: "127.0.0.1", lambda: "kenpi"))

        def fail(_):
            raise socket.gaierror("no")

        self.assertFalse(_hostname_resolves(fail, lambda: "kenpi"))
        block = threading.Event()
        self.addCleanup(block.set)
        self.assertIsNone(
            _hostname_resolves(lambda n: block.wait(5), lambda: "kenpi", timeout=0.1)
        )

    def test_check_statuses(self):
        from kenpi.diagnostics.checks import check_network_routes
        from kenpi.diagnostics.models import DiagnosticContext
        from kenpi.diagnostics.models import Status

        def status(value):
            results = check_network_routes(
                DiagnosticContext(
                    host_health=lambda: {},
                    network=lambda: {"hostname_resolves": value},
                )
            )
            return next(r for r in results if r.name == "dns_resolution").status

        self.assertIs(status(True), Status.OK)
        self.assertIs(status(False), Status.WARN)
        self.assertIs(status(None), Status.UNKNOWN)


if __name__ == "__main__":
    unittest.main()


class UsbTests(unittest.TestCase):
    def make(self, root: Path) -> Path:
        usb = root / "bus/usb/devices"
        (usb / "usb1").mkdir(parents=True)
        for name, vid, pid, bound in (
            ("1-1", "0d8c", "0014", True),
            ("1-2", "dead", "beef", False),
        ):
            device = usb / name
            device.mkdir()
            (device / "idVendor").write_text(vid)
            (device / "idProduct").write_text(pid)
            (device / "speed").write_text("480")
            interface = usb / f"{name}:1.0"
            interface.mkdir()
            if bound:
                (interface / "driver").mkdir()
            (device / f"{name}:1.0").mkdir()
            if bound:
                (device / f"{name}:1.0" / "driver").mkdir()
        return root

    def test_reads_devices_and_flags_unbound(self):
        from kenpi.diagnostics.system import read_usb_devices

        with tempfile.TemporaryDirectory() as d:
            devices = read_usb_devices(self.make(Path(d)))
            self.assertEqual([x["name"] for x in devices], ["1-1", "1-2"])
            self.assertEqual(devices[0]["unbound_interfaces"], 0)
            self.assertEqual(devices[1]["unbound_interfaces"], 1)
            ctx = DiagnosticContext(
                host_health=lambda: {},
                system=lambda: {
                    "usb_devices": ["1-1", "1-2"],
                    "usb_details": devices,
                },
            )
            usb = {r.name: r for r in check_system(ctx)}["usb"]
            self.assertEqual(usb.status, Status.WARN)
            self.assertIn("dead:beef", usb.detail)

    def test_unreadable_bus_is_none(self):
        from kenpi.diagnostics.system import read_usb_devices

        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(read_usb_devices(Path(d)))

    def test_unbound_maps_to_fault(self):
        from kenpi.diagnostics.faults import detect_faults
        from kenpi.diagnostics.models import CheckResult, Subsystem

        faults = detect_faults(
            [CheckResult(Subsystem.HOST, "usb", Status.WARN, "x", {})], None
        )
        self.assertEqual([f.id for f in faults], ["host.usb_unbound"])

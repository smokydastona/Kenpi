import tempfile
import unittest
from pathlib import Path

from kenpi.management.broker import Broker, BrokerError
from tests.test_gateway import GatewayCase

UNITS = frozenset({"kenpi-status.service", "kenpi-backup.timer"})
READ = frozenset({"ops.read"})
BOTH = frozenset({"ops.read", "ops.manage"})


class FakeRunner:
    def __init__(self, code=0, out="active"):
        self.calls, self.code, self.out = [], code, out

    def __call__(self, argv, timeout):
        self.calls.append(argv)
        return self.code, self.out


def make(runner=None, proc=None, clock=None):
    log = []
    broker = Broker(
        lambda action, **f: log.append((action, f)),
        UNITS,
        runner or FakeRunner(),
        proc or Path("/proc"),
        clock or (lambda: 1000.0),
    )
    return broker, log


class BrokerTests(unittest.TestCase):
    def test_read_needs_scope_and_allowlisted_unit(self):
        broker, _ = make()
        out = broker.run("d", READ, "service_status", {"unit": "kenpi-status.service"})
        self.assertEqual(out["result"]["active"], "active")
        for args in ({"unit": "ssh.service"}, {"unit": "a; reboot"}, {}, {"unit": 3}):
            with self.assertRaises(BrokerError) as ctx:
                broker.run("d", READ, "service_status", args)
            self.assertEqual(ctx.exception.code, "bad_arguments")
        with self.assertRaises(BrokerError) as ctx:
            broker.run(
                "d",
                frozenset({"status.read"}),
                "service_status",
                {"unit": "kenpi-status.service"},
            )
        self.assertEqual(ctx.exception.status, 403)

    def test_unknown_operation_and_non_object_args(self):
        broker, _ = make()
        with self.assertRaises(BrokerError) as ctx:
            broker.run("d", BOTH, "format_disk", {})
        self.assertEqual(ctx.exception.status, 404)
        with self.assertRaises(BrokerError):
            broker.run("d", BOTH, "list_services", [])

    def test_restart_requires_manage_and_confirmation(self):
        runner = FakeRunner()
        broker, log = make(runner)
        args = {"unit": "kenpi-status.service"}
        with self.assertRaises(BrokerError):
            broker.run("d", READ, "service_restart", args)
        first = broker.run("d", BOTH, "service_restart", args)
        self.assertEqual(first["status"], "confirmation_required")
        self.assertEqual(runner.calls, [])
        done = broker.run("d", BOTH, "service_restart", args, first["confirm_token"])
        self.assertEqual(done["status"], "done")
        self.assertEqual(
            runner.calls, [["systemctl", "restart", "kenpi-status.service"]]
        )
        self.assertIn("op_done", [a for a, _ in log])

    def test_power_operations_need_confirmation(self):
        for op, verb in (("reboot", "reboot"), ("shutdown", "poweroff")):
            runner = FakeRunner()
            broker, _ = make(runner)
            with self.assertRaises(BrokerError):
                broker.run("d", READ, op, {})
            first = broker.run("d", BOTH, op, {})
            self.assertEqual(first["status"], "confirmation_required")
            self.assertEqual(runner.calls, [])
            with self.assertRaises(BrokerError):
                broker.run("d", BOTH, op, {"force": True})
            broker.run("d", BOTH, op, {}, first["confirm_token"])
            self.assertEqual(runner.calls, [["systemctl", verb]])

    def test_desktop_status_is_read_only_and_never_claims_streaming(self):
        import socket

        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        try:
            from kenpi.management.broker import probe_desktop

            up = probe_desktop(port)
        finally:
            listener.close()
        down = probe_desktop(port)
        self.assertTrue(up["listening_on_loopback"])
        self.assertFalse(down["listening_on_loopback"])
        self.assertFalse(up["streaming_available"])
        broker, _ = make()
        broker.desktop_probe = lambda: {"ok": False}
        done = broker.run("d", READ, "desktop_status", {})
        self.assertEqual(done["result"], {"ok": False})

    def test_polkit_rule_matches_wired_units(self):
        root = Path(__file__).resolve().parents[1]
        rule = (root / "deploy/50-kenpi-broker.rules").read_text()
        main = (root / "kenpi/main.py").read_text()
        for unit in (
            "kenpi-status.service",
            "kenpi-backup.service",
            "kenpi-backup.timer",
        ):
            self.assertIn(f'"{unit}"', rule)
            self.assertIn(f'"{unit}"', main)
        self.assertIn('subject.user !== "kenpi"', rule)

    def test_token_is_single_use_and_bound(self):
        broker, _ = make()
        args = {"unit": "kenpi-status.service"}
        token = broker.run("d", BOTH, "service_restart", args)["confirm_token"]
        for device, other in (("e", args), ("d", {"unit": "kenpi-backup.timer"})):
            with self.assertRaises(BrokerError):
                broker.run(device, BOTH, "service_restart", other, token)
        token = broker.run("d", BOTH, "service_restart", args)["confirm_token"]
        broker.run("d", BOTH, "service_restart", args, token)
        with self.assertRaises(BrokerError) as ctx:
            broker.run("d", BOTH, "service_restart", args, token)
        self.assertEqual(ctx.exception.code, "confirmation_invalid")

    def test_token_expires(self):
        now = [1000.0]
        broker, _ = make(clock=lambda: now[0])
        args = {"unit": "kenpi-status.service"}
        token = broker.run("d", BOTH, "service_restart", args)["confirm_token"]
        now[0] += 61
        with self.assertRaises(BrokerError):
            broker.run("d", BOTH, "service_restart", args, token)

    def test_failed_restart_is_reported_not_hidden(self):
        broker, _ = make(FakeRunner(1, "Access denied"))
        args = {"unit": "kenpi-status.service"}
        token = broker.run("d", BOTH, "service_restart", args)["confirm_token"]
        out = broker.run("d", BOTH, "service_restart", args, token)
        self.assertFalse(out["result"]["ok"])
        self.assertIn("denied", out["result"]["detail"])

    def test_invalid_allowlist_rejected(self):
        with self.assertRaises(ValueError):
            Broker(lambda *a, **k: None, frozenset({"ssh; reboot"}))

    def test_process_list_reads_proc(self):
        with tempfile.TemporaryDirectory() as d:
            for pid, name, pages in ((10, "big", 500), (11, "small", 5)):
                p = Path(d) / str(pid)
                p.mkdir()
                (p / "comm").write_text(name + "\n")
                (p / "statm").write_text(f"1 {pages} 0 0 0 0 0")
            (Path(d) / "self").mkdir()
            broker, _ = make(proc=Path(d))
            procs = broker.run("d", READ, "process_list", {})["result"]["processes"]
            self.assertEqual([p["name"] for p in procs], ["big", "small"])


class GatewayOpsTests(GatewayCase):
    async def asyncSetUp(self):
        self.runner = FakeRunner()
        broker, self.log = make(self.runner)
        type(self).kwargs = {"broker": broker}
        await super().asyncSetUp()

    async def asyncTearDown(self):
        type(self).kwargs = {}

    def auth(self, creds):
        return {"authorization": f"Bearer {creds['access_token']}"}

    async def test_requires_auth_and_scope(self):
        status, _ = await self.request("GET", "/api/v1/ops")
        self.assertEqual(status, 401)
        creds = await self.enroll(scopes={"status.read"})
        status, body = await self.request(
            "GET", "/api/v1/ops", headers=self.auth(creds)
        )
        self.assertEqual((status, body["operations"]), (200, []))
        status, body = await self.request(
            "POST", "/api/v1/ops/run", {"operation": "list_services"}, self.auth(creds)
        )
        self.assertEqual((status, body["error"]), (403, "scope_required"))

    async def test_confirmed_restart_over_https(self):
        creds = await self.enroll(scopes={"status.read", "ops.read", "ops.manage"})
        h = self.auth(creds)
        body = {"operation": "service_restart", "args": {"unit": "kenpi-backup.timer"}}
        status, first = await self.request("POST", "/api/v1/ops/run", body, h)
        self.assertEqual((status, first["status"]), (200, "confirmation_required"))
        self.assertEqual(self.runner.calls, [])
        status, done = await self.request(
            "POST",
            "/api/v1/ops/run",
            {**body, "confirm_token": first["confirm_token"]},
            h,
        )
        self.assertEqual((status, done["status"]), (200, "done"))
        status, bad = await self.request(
            "POST",
            "/api/v1/ops/run",
            {"operation": "service_status", "args": {"unit": "x.service"}},
            h,
        )
        self.assertEqual((status, bad["error"]), (400, "bad_arguments"))


if __name__ == "__main__":
    unittest.main()

import contextlib
import io
import os
import stat
import tempfile
import unittest
from pathlib import Path

from kenpi.core.config import KenpiConfig, load_config
from kenpi.core.errors import ConfigurationError
from kenpi.remote.wireguard import (
    RemoteAccess,
    RemoteState,
    render_config,
    valid_wireguard_key,
)
from kenpi.security.devices import DeviceRegistry
from kenpi.tools import devices as cli
from tests.gateway_helpers import DeviceKey


def wg_public():
    import base64

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

    raw = (
        X25519PrivateKey.generate()
        .public_key()
        .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    )
    return base64.b64encode(raw).decode()


class WireGuardTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.registry = DeviceRegistry(self.dir / "gw")
        self.calls = []
        self.rc = {}
        self.access = RemoteAccess(
            self.registry,
            RemoteState(self.dir / "gw"),
            "10.77.0.0/24",
            51820,
            self.dir / "server.key",
            self.dir / "wg.conf",
            runner=self._run,
            capture=self._capture,
        )
        self.dump = None  # None: interface absent

    def _run(self, cmd):
        self.calls.append(list(cmd))
        return self.rc.get(cmd[0] if cmd[0] != "wg" else " ".join(cmd[:2]), 0)

    def _capture(self, cmd):
        self.calls.append(list(cmd))
        return (1, "") if self.dump is None else (0, self.dump)

    @staticmethod
    def dump_text(peers=()):
        lines = ["PRIV\tSERVERPUB\t51820\toff"]
        for key, ips, hs in peers:
            lines.append(f"{key}\t(none)\t(none)\t{ips}\t{hs}\t10\t20\toff")
        return "\n".join(lines) + "\n"

    def enrol(self, name, remote=True, wg=True):
        key = DeviceKey()
        eid, challenge, _ = self.registry.begin_pairing(allow_remote=remote)
        self.registry.submit_enrollment(
            eid, challenge, name, key.public, key.enroll_proof(eid, challenge, name),
            wireguard_key=wg_public() if wg else None,
        )  # fmt: skip
        self.registry.approve(eid)
        return self.registry.collect(eid, key.collect_proof(eid))[1].device_id

    def test_disabled_by_default(self):
        self.assertFalse(RemoteState(self.dir / "gw").enabled())
        self.access.apply()
        self.assertEqual(self.calls, [["wg", "show", "wg", "dump"]])

    def test_config_has_no_private_key_and_stable_addresses(self):
        a, b = self.enrol("a"), self.enrol("b")
        text = self.access.write_config()
        self.assertNotIn("PrivateKey", text)
        self.assertIn("PostUp = wg set %i private-key", text)
        self.assertIn("AllowedIPs = 10.77.0.2/32", text)
        self.registry.revoke(a)
        text = self.access.write_config()
        self.assertEqual(text.count("[Peer]"), 1)
        self.assertIn("AllowedIPs = 10.77.0.3/32", text)  # b keeps its address
        self.assertEqual(stat.S_IMODE(os.stat(self.dir / "wg.conf").st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.dir / "server.key").st_mode), 0o600)
        self.assertTrue(b)

    def test_only_remote_permitted_devices_with_keys_are_peers(self):
        self.enrol("local", remote=False)
        self.enrol("nokey", wg=False)
        self.assertEqual(self.access.write_config().count("[Peer]"), 0)

    def test_enable_brings_tunnel_up_and_revocation_removes_peer_incrementally(self):
        device = self.enrol("a")
        self.access.enable()
        self.assertTrue(RemoteState(self.dir / "gw").enabled())
        self.assertIn(["wg-quick", "up", str(self.dir / "wg.conf")], self.calls)
        key, address = self.registry.wireguard_peers("10.77.0.0/24")[device]
        self.dump = self.dump_text([(key, f"{address}/32", 0)])
        self.calls.clear()
        self.assertFalse(
            self.access.reconcile()
        )  # nothing changed, no commands applied
        self.registry.revoke(device)
        self.calls.clear()
        self.assertTrue(self.access.reconcile())
        self.assertIn(["wg", "set", "wg", "peer", key, "remove"], self.calls)
        self.assertFalse(any("syncconf" in c for c in self.calls))

    def test_new_device_is_added_to_running_tunnel(self):
        self.access.enable()
        self.dump = self.dump_text()
        device = self.enrol("late")
        self.calls.clear()
        self.assertTrue(self.access.reconcile())
        key, address = self.registry.wireguard_peers("10.77.0.0/24")[device]
        self.assertIn(["wg", "set", "wg", "peer", key, "allowed-ips", f"{address}/32"], self.calls)  # fmt: skip

    def test_diagnostics_reports_drift_and_exposure(self):
        device = self.enrol("a")
        report = self.access.diagnostics()
        self.assertFalse(report["up"])
        self.assertEqual(report["problems"], [])  # disabled and down is consistent
        self.access.enable()
        report = self.access.diagnostics()
        self.assertIn("remote access is enabled but the tunnel interface is down", report["problems"])  # fmt: skip
        key, _ = self.registry.wireguard_peers("10.77.0.0/24")[device]
        self.dump = self.dump_text([(key, "10.77.0.2/32", 100), ("STRANGER" + "A" * 35 + "=", "10.77.0.9/32", 0)])  # fmt: skip
        report = self.access.diagnostics(now=160)
        self.assertEqual(report["peers"][0]["handshake_age_seconds"], 60)
        self.assertEqual(report["peers"][0]["device_id"], device)
        self.assertIsNone(report["peers"][1]["device_id"])
        self.assertTrue(any("not an active permitted device" in p for p in report["problems"]))  # fmt: skip
        self.assertNotIn("PRIV", str(report))

    def test_client_parameters_contain_no_secrets(self):
        from kenpi.remote.wireguard import client_parameters, render_client_config

        device = self.enrol("a")
        params = client_parameters(self.registry, "10.77.0.0/24", 51820, self.dir / "server.key", device)  # fmt: skip
        self.assertIsNone(params)  # server key not created until remote is set up
        self.access.write_config()
        params = client_parameters(self.registry, "10.77.0.0/24", 51820, self.dir / "server.key", device)  # fmt: skip
        self.assertEqual(params["allowed_ips"], ["10.77.0.1/32"])
        text = render_client_config(params, "kenpi.example")
        self.assertIn("AllowedIPs = 10.77.0.1/32", text)
        self.assertIn("PrivateKey = <PRIVATE_KEY_STAYS_ON_DEVICE>", text)

    def test_up_failure_raises(self):
        self.rc["wg show"] = 1
        self.rc["wg-quick"] = 1
        with self.assertRaises(RuntimeError):
            self.access.enable()

    def test_key_validation_and_render_rejects_bad_input(self):
        self.assertTrue(valid_wireguard_key(wg_public()))
        self.assertFalse(valid_wireguard_key("short"))
        with self.assertRaises(ValueError):
            render_config("8.8.8.0/24", 51820, "/k", {})
        with self.assertRaises(ValueError):
            render_config("10.77.0.0/24", 51820, "/k", {"d": ("bad", "10.77.0.2")})

    def test_enrolment_with_wireguard_key_persists(self):
        device = self.enrol("x")
        self.assertIn(device, self.registry.wireguard_peers("10.77.0.0/24"))


class GatewayConfigTests(unittest.TestCase):
    def test_defaults_disabled(self):
        self.assertFalse(KenpiConfig().gateway_enabled)

    def test_enabled_requires_explicit_private_hosts(self):
        with self.assertRaises(ConfigurationError):
            KenpiConfig(gateway_enabled=True)
        for host in ("0.0.0.0", "8.8.8.8"):
            with self.assertRaises(ConfigurationError):
                KenpiConfig(gateway_enabled=True, gateway_hosts=(host,))
        config = KenpiConfig(gateway_enabled=True, gateway_hosts=("192.168.1.5",))
        self.assertEqual(config.gateway_hosts, ("192.168.1.5",))

    def test_hosts_without_enable_and_bad_values_rejected(self):
        with self.assertRaises(ConfigurationError):
            KenpiConfig(gateway_hosts=("192.168.1.5",))
        with self.assertRaises(ConfigurationError):
            KenpiConfig(wireguard_network="8.8.8.0/24")
        with self.assertRaises(ConfigurationError):
            KenpiConfig(state_directory="relative")

    def test_unauthenticated_api_still_loopback_only(self):
        with self.assertRaises(ConfigurationError):
            KenpiConfig(api_host="192.168.1.5")

    def test_toml_list_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "c.toml")
            path.write_text(
                '[kenpi]\ngateway_enabled = true\ngateway_hosts = ["10.0.0.5"]\n'
            )
            self.assertEqual(load_config(path).gateway_hosts, ("10.0.0.5",))


class CliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.conf = Path(self._tmp.name, "kenpi.toml")
        self.conf.write_text(
            f'[kenpi]\nstate_directory = "{self._tmp.name}/state"\n'
            'gateway_enabled = true\ngateway_hosts = ["127.0.0.1"]\n'
            'wireguard_network = "10.77.0.0/24"\n'
        )

    def run_cli(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(["--config", str(self.conf), *args])
        return code, out.getvalue()

    def test_pair_approve_list_revoke_flow(self):
        code, out = self.run_cli("pair")
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("kenpi://pair?d="))
        registry = DeviceRegistry(Path(self._tmp.name, "state", "gateway"))
        self.assertEqual(self.run_cli("pending")[1], "")
        key = DeviceKey()
        import base64
        import json

        payload = json.loads(
            base64.urlsafe_b64decode(out.split("d=")[1].split()[0] + "==")
        )
        self.assertEqual(payload["address"], "127.0.0.1")
        self.assertEqual(len(payload["fingerprint"]), 64)
        eid, challenge = payload["enrollment_id"], payload["challenge"]
        registry.submit_enrollment(
            eid,
            challenge,
            "Phone",
            key.public,
            key.enroll_proof(eid, challenge, "Phone"),
        )
        self.assertIn("Phone", self.run_cli("pending")[1])
        self.assertEqual(self.run_cli("approve", eid)[0], 0)
        _, tokens = registry.collect(eid, key.collect_proof(eid))
        self.assertIn(tokens.device_id, self.run_cli("list")[1])
        self.run_cli("revoke", tokens.device_id)
        self.assertIn('"revoked": true', self.run_cli("list")[1])

    def test_remote_status_default_off(self):
        self.assertEqual(
            self.run_cli("remote", "status")[1].strip(), '{"enabled": false}'
        )

    def test_errors_return_nonzero(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.run_cli("approve", "missing")[0], 1)
            self.assertEqual(self.run_cli("revoke", "missing")[0], 1)


if __name__ == "__main__":
    unittest.main()

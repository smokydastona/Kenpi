"""Live WireGuard integration tests: a real tunnel between two network namespaces.

Opt-in (``KENPI_LIVE_WG=1``), needs root, ``ip``, ``wg``, ``wg-quick`` and either the kernel
WireGuard module or ``wireguard-go`` plus ``/dev/net/tun``. It builds three namespaces
(Pi, remote phone, unrelated "internet" host) joined by veth pairs, runs the real gateway,
the real CLI and the real reconcile watcher in the Pi namespace, and drives a reference
client from the phone namespace over the encrypted tunnel.

This proves the software against a real WireGuard data path on one machine. It is NOT
evidence about a Raspberry Pi kernel, a cellular/NAT path, or the Android WireGuard client.
"""

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRV, PH, PUB = "kpisrv", "kpiph", "kpipub"
LAN_SRV, LAN_PH = "192.168.77.1", "192.168.77.2"
WAN_SRV, WAN_PH = "203.0.113.1", "203.0.113.2"
PUB_SRV, PUB_PEER = "198.51.100.1", "198.51.100.2"
TUN_GW = "10.77.0.1"
GW_PORT, API_PORT = 8443, 18080

TOOLS = all(shutil.which(t) for t in ("ip", "wg", "wg-quick"))
ENABLED = os.environ.get("KENPI_LIVE_WG") == "1" and os.geteuid() == 0 and TOOLS


def sh(*cmd, ns=None, check=True, env=None, timeout=60, **kw):
    argv = ["ip", "netns", "exec", ns, *cmd] if ns else list(cmd)
    result = subprocess.run(
        argv, check=False, capture_output=True, text=True, timeout=timeout,
        env={**os.environ, **(env or {})}, cwd=REPO, **kw,
    )  # fmt: skip
    if check and result.returncode:
        diag = subprocess.run(
            "id; ls -laR " + str(REPO.parent / "x") + " 2>&1 | head -0; "
            "ls -laZ /tmp/kenpi-livewg-* 2>&1 | head -30; "
            "ls -la /tmp/kenpi-livewg-*/state/gateway/wireguard 2>&1; "
            "findmnt -T /tmp 2>&1; aa-status 2>&1 | grep -i -E \"wg|wire|python|bash\"; dmesg 2>&1 | grep -i denied | tail -5; head -c0 /tmp/kenpi-livewg-*/state/gateway/wireguard/wg-kenpi.conf 2>&1; cat /tmp/kenpi-livewg-*/state/gateway/wireguard/wg-kenpi.conf | sed -n 1,3p | cut -c1-12",
            shell=True, capture_output=True, text=True,
        ).stdout  # fmt: skip
        result.stderr += "\n--- diagnostics ---\n" + diag
        raise RuntimeError(
            f"{argv} exited {result.returncode}\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
    return result


def wait_for(predicate, timeout=20.0, interval=0.4, what="condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError(f"timed out waiting for {what}")


@unittest.skipUnless(
    ENABLED, "set KENPI_LIVE_WG=1 and run as root with wg/ip installed"
)
class LiveWireGuardTests(unittest.TestCase):
    """Ordered scenario: later tests rely on state built by earlier ones."""

    env = {
        "PYTHONPATH": str(REPO),
        "WG_QUICK_USERSPACE_IMPLEMENTATION": "wireguard-go",
        "WG_PROCESS_FOREGROUND": "0",
    }

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="kenpi-livewg-"))
        cls.procs = []
        os.makedirs("/dev/net", exist_ok=True)
        if not os.path.exists("/dev/net/tun"):
            os.mknod("/dev/net/tun", 0o600 | 0o020000, os.makedev(10, 200))
        cls.cleanup_topology()
        try:
            cls.build_topology()
            cls.start_services()
            cls.pair_phone()
        except BaseException:
            cls.tearDownClass()
            raise

    # ---- topology ---------------------------------------------------------------

    @classmethod
    def cleanup_topology(cls):
        for ns in (SRV, PH, PUB):
            for iface in ("wg-kenpi", "wg-phone"):
                sh("wg-quick", "down", str(cls.tmp / f"{iface}.conf"), ns=ns, check=False,
                   env=cls.env)  # fmt: skip
            sh("ip", "netns", "del", ns, check=False)

    @classmethod
    def build_topology(cls):
        for ns in (SRV, PH, PUB):
            sh("ip", "netns", "add", ns)
            sh("ip", "link", "set", "lo", "up", ns=ns)
        for a_ns, a, a_ip, b_ns, b, b_ip in (
            (SRV, "lan0", LAN_SRV, PH, "lan1", LAN_PH),
            (SRV, "wan0", WAN_SRV, PH, "wan1", WAN_PH),
            (SRV, "pub0", PUB_SRV, PUB, "pub1", PUB_PEER),
        ):
            sh("ip", "link", "add", a, "type", "veth", "peer", "name", b)
            sh("ip", "link", "set", a, "netns", a_ns)
            sh("ip", "link", "set", b, "netns", b_ns)
            for ns, name, ip in ((a_ns, a, a_ip), (b_ns, b, b_ip)):
                sh("ip", "addr", "add", f"{ip}/24", "dev", name, ns=ns)
                sh("ip", "link", "set", name, "up", ns=ns)

    @classmethod
    def write_config(cls):
        cfg = cls.tmp / "kenpi.toml"
        cfg.write_text(
            "[kenpi]\n"
            f"api_port = {API_PORT}\n"
            "gateway_enabled = true\n"
            f'gateway_hosts = ["{LAN_SRV}", "{TUN_GW}"]\n'
            f"gateway_port = {GW_PORT}\n"
            f'state_directory = "{cls.tmp / "state"}"\n'
            'wireguard_network = "10.77.0.0/24"\n'
            "wireguard_port = 51820\n"
        )
        (cls.tmp / "state").mkdir(exist_ok=True)
        return cfg

    @classmethod
    def start_services(cls):
        cls.cfg = cls.write_config()
        cls.gateway_log = open(cls.tmp / "gateway.log", "w")
        cls.procs.append(
            subprocess.Popen(
                ["ip", "netns", "exec", SRV, sys.executable, "-m", "kenpi.main", "--config", str(cls.cfg)],
                cwd=REPO, env={**os.environ, **cls.env}, stdout=cls.gateway_log, stderr=subprocess.STDOUT,
            )
        )  # fmt: skip
        wait_for(lambda: "Local status service started" in (cls.tmp / "gateway.log").read_text(),
                 what="gateway start")  # fmt: skip

    # ---- helpers ----------------------------------------------------------------

    @classmethod
    def admin(cls, *args, check=True):
        return sh(sys.executable, "-m", "kenpi.tools.devices", "--config", str(cls.cfg),
                  *args, ns=SRV, env=cls.env, check=check)  # fmt: skip

    @classmethod
    def phone(cls, cmd, *args, ns=PH):
        done = sh(sys.executable, "-m", "tests.live_client", cmd, "--state", str(cls.tmp / "phone.json"),
                  *args, ns=ns, env=cls.env, check=False)  # fmt: skip
        try:
            return json.loads(done.stdout.strip().splitlines()[-1])
        except (IndexError, ValueError):
            raise AssertionError(
                f"client output unusable: {done.stdout!r} {done.stderr[-400:]!r}"
            )

    @classmethod
    def wg_show(cls, ns, iface):
        done = sh("wg", "show", iface, "dump", ns=ns, env=cls.env, check=False)
        return done.returncode, done.stdout

    @classmethod
    def pair_phone(cls):
        out = cls.admin("pair", "--allow-remote", "--host", LAN_SRV).stdout
        payload = next(line for line in out.splitlines() if line.startswith("kenpi://"))
        result = cls.phone("enroll", "--payload", payload, "--name", "live-phone")
        assert result.get("status") == 200, result
        pending = json.loads(cls.admin("pending").stdout.splitlines()[0])
        cls.admin("approve", pending["enrollment_id"])
        # Start the tunnel before the device is collected; the watcher must add the peer itself.
        cls.admin("remote", "enable")
        cls.procs.append(
            subprocess.Popen(
                ["ip", "netns", "exec", SRV, sys.executable, "-m", "kenpi.tools.devices", "--config",
                 str(cls.cfg), "remote", "watch", "--interval", "1"],
                cwd=REPO, env={**os.environ, **cls.env}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        )  # fmt: skip
        got = cls.phone("collect")
        assert got["state"] == "collected", got
        cls.device_id = got["device_id"]
        conf = cls.tmp / "wg-phone.conf"
        wgc = cls.phone(
            "wgconf", "--out", str(conf), "--endpoint", WAN_SRV, "--host", LAN_SRV
        )
        assert wgc["status"] == 200, wgc
        cls.phone_conf = conf
        cls.tunnel_up()

    @classmethod
    def tunnel_up(cls):
        sh("wg-quick", "up", str(cls.phone_conf), ns=PH, env=cls.env)

    @classmethod
    def tunnel_down(cls):
        sh("wg-quick", "down", str(cls.phone_conf), ns=PH, env=cls.env, check=False)

    @classmethod
    def tearDownClass(cls):
        for proc in cls.procs:
            proc.terminate()
        for proc in cls.procs:
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()
        cls.tunnel_down() if hasattr(cls, "phone_conf") else None
        sh("wg-quick", "down", str(cls.tmp / "state/gateway/wireguard/wg-kenpi.conf"), ns=SRV,
           env=cls.env, check=False)  # fmt: skip
        for ns in (SRV, PH, PUB):
            sh("ip", "netns", "del", ns, check=False)
        if hasattr(cls, "gateway_log"):
            cls.gateway_log.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def gateway_text(self):
        return (self.tmp / "gateway.log").read_text()

    def over_tunnel(self, cmd, *extra):
        return self.phone(cmd, "--host", TUN_GW, *extra)

    def handshake_age(self):
        code, out = self.wg_show(SRV, "wg-kenpi")
        if code != 0:
            return None
        peers = [row.split("\t") for row in out.strip().splitlines()[1:]]
        return (
            None
            if not peers or peers[0][4] == "0"
            else int(time.time()) - int(peers[0][4])
        )

    # ---- scenario ---------------------------------------------------------------

    def test_01_watcher_added_peer_and_tunnel_handshakes(self):
        self.assertTrue(
            wait_for(lambda: self.wg_show(SRV, "wg-kenpi")[0] == 0 and len(self.wg_show(SRV, "wg-kenpi")[1].splitlines()) == 2,
                     what="peer added by the watcher"))  # fmt: skip
        sh(
            "ip", "link", "set", "lan1", "down", ns=PH
        )  # from now on only the tunnel reaches the Pi
        result = wait_for(lambda: (r := self.over_tunnel("snapshot")).get("status") == 200 and r, 30, what="REST via tunnel")  # fmt: skip
        self.assertEqual(result["type"], "snapshot")
        self.assertLess(self.handshake_age(), 30)
        self.assertEqual(self.admin("remote", "diagnose", check=False).returncode, 0)

    def test_02_gateway_not_reachable_without_the_tunnel(self):
        # Phone has only the "internet" path to the Pi's wan address, where nothing listens.
        for host in (WAN_SRV,):
            out = self.phone("snapshot", "--host", host)
            self.assertIn(
                out.get("error"),
                {"ConnectionRefusedError", "TimeoutError", "OSError"},
                out,
            )

    def test_03_websocket_over_tunnel_snapshot_and_heartbeat(self):
        log = self.tmp / "ws.log"
        proc = subprocess.Popen(
            ["ip", "netns", "exec", PH, sys.executable, "-m", "tests.live_client", "ws", "--state",
             str(self.tmp / "phone.json"), "--host", TUN_GW, "--seconds", "400", "--log", str(log)],
            cwd=REPO, env={**os.environ, **self.env}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )  # fmt: skip
        type(self).ws_proc, type(self).ws_log = proc, log
        self.procs.append(proc)

        def events():
            return (
                [json.loads(row) for row in log.read_text().splitlines()]
                if log.exists()
                else []
            )

        type(self).ws_events = staticmethod(events)
        wait_for(
            lambda: any(e.get("type") == "snapshot" for e in events()),
            20,
            what="WS snapshot",
        )
        wait_for(
            lambda: any(e.get("type") == "heartbeat" for e in events()),
            30,
            what="WS heartbeat",
        )

    def test_04_tunnel_interruption_reconnects_with_resume(self):
        before = len([e for e in self.ws_events() if e.get("event") == "connected"])
        self.tunnel_down()
        wait_for(lambda: any(e.get("event") == "error" for e in self.ws_events()), 30, what="client notices outage")  # fmt: skip
        self.assertNotEqual(self.over_tunnel("snapshot").get("status"), 200)
        self.tunnel_up()
        wait_for(lambda: len([e for e in self.ws_events() if e.get("event") == "connected"]) > before, 40, what="WS reconnect")  # fmt: skip
        wait_for(lambda: any(e.get("type") == "resumed" for e in self.ws_events()), 20, what="resume, not a silent full replay")  # fmt: skip
        self.assertEqual(wait_for(lambda: (r := self.over_tunnel("snapshot")).get("status") == 200 and r, 20)["type"], "snapshot")  # fmt: skip

    def test_05_wan_link_flap_recovers(self):
        sh("ip", "link", "set", "wan1", "down", ns=PH)
        time.sleep(4)
        sh("ip", "link", "set", "wan1", "up", ns=PH)
        sh("ip", "addr", "replace", f"{WAN_PH}/24", "dev", "wan1", ns=PH)
        wait_for(lambda: self.over_tunnel("snapshot").get("status") == 200, 40, what="tunnel recovery after link flap")  # fmt: skip

    def test_06_no_public_exposure(self):
        for port in (GW_PORT, API_PORT, 22, 80):
            out = sh(sys.executable, "-c",
                     "import socket,sys;s=socket.socket();s.settimeout(3)\n"
                     f"sys.exit(0 if s.connect_ex(('{PUB_SRV}',{port}))==0 else 1)",
                     ns=PUB, check=False)  # fmt: skip
            self.assertEqual(out.returncode, 1, f"{PUB_SRV}:{port} accepted a connection from an unrelated host")  # fmt: skip
        listeners = sh("ss", "-ltnH", ns=SRV).stdout.split()
        addrs = {
            tok for tok in listeners if ":" in tok and tok.rsplit(":", 1)[1].isdigit()
        }
        allowed = {
            f"{LAN_SRV}:{GW_PORT}",
            f"{TUN_GW}:{GW_PORT}",
            f"127.0.0.1:{API_PORT}",
        }
        self.assertTrue(addrs <= allowed | {"0.0.0.0:*", "*:*", "[::]:*"}, f"unexpected listeners: {addrs - allowed}")  # fmt: skip
        self.assertFalse({a for a in addrs if a.startswith(("0.0.0.0:", "*:", "[::]:", PUB_SRV, WAN_SRV))})  # fmt: skip
        # Pairing is a local-only operation: refused when attempted through the tunnel.
        fingerprint = json.loads((self.tmp / "phone.json").read_text())["fingerprint"]
        probe = {"v": 1, "address": TUN_GW, "port": GW_PORT, "fingerprint": fingerprint,
                 "enrollment_id": "probe", "challenge": "probe", "expires": 0}  # fmt: skip
        encoded = (
            base64.urlsafe_b64encode(json.dumps(probe).encode()).decode().rstrip("=")
        )
        probe_state = self.tmp / "probe.json"
        done = sh(sys.executable, "-m", "tests.live_client", "enroll", "--state", str(probe_state),
                  "--payload", f"kenpi://pair?d={encoded}", "--name", "probe", "--host", TUN_GW,
                  ns=PH, env=self.env, check=False)  # fmt: skip
        reply = json.loads(done.stdout.strip().splitlines()[-1])
        self.assertEqual(reply.get("status"), 403, reply)

    def test_07_per_device_remote_permission_and_local_access(self):
        self.admin("deny-remote", self.device_id)
        wait_for(lambda: self.wg_show(SRV, "wg-kenpi")[1].count("\n") == 1, 15, what="peer removed from the tunnel")  # fmt: skip
        # The tunnel is already cut, so the client cannot see the close frame; the server log proves it closed.
        wait_for(lambda: "WebSocket closed zone=remote code=4403" in self.gateway_text(), 20, what="server closed remote WS")  # fmt: skip
        sh("ip", "link", "set", "lan1", "up", ns=PH)
        self.assertEqual(
            self.phone("snapshot", "--host", LAN_SRV).get("status"), 200
        )  # still valid locally
        self.assertNotEqual(self.over_tunnel("snapshot").get("status"), 200)
        self.admin("allow-remote", self.device_id)
        wait_for(
            lambda: self.wg_show(SRV, "wg-kenpi")[1].count("\n") == 2,
            15,
            what="peer restored",
        )
        sh("ip", "link", "set", "lan1", "down", ns=PH)
        wait_for(lambda: self.over_tunnel("snapshot").get("status") == 200, 40, what="remote access restored")  # fmt: skip

    def test_08_remote_disabled_drops_the_tunnel_but_not_local_access(self):
        self.admin("remote", "disable")
        wait_for(
            lambda: self.wg_show(SRV, "wg-kenpi")[0] != 0,
            15,
            what="tunnel interface removed",
        )
        self.assertNotEqual(self.over_tunnel("snapshot").get("status"), 200)
        sh("ip", "link", "set", "lan1", "up", ns=PH)
        self.assertEqual(self.phone("snapshot", "--host", LAN_SRV).get("status"), 200)
        sh("ip", "link", "set", "lan1", "down", ns=PH)
        self.admin("remote", "enable")
        self.assertEqual(self.wg_show(SRV, "wg-kenpi")[0], 0)
        wait_for(lambda: self.over_tunnel("snapshot").get("status") == 200, 40, what="remote access re-enabled")  # fmt: skip

    def test_09_revocation_is_immediate_everywhere(self):
        self.admin("revoke", self.device_id)
        wait_for(lambda: self.wg_show(SRV, "wg-kenpi")[1].count("\n") == 1, 15, what="peer removed from the tunnel")  # fmt: skip
        self.assertNotEqual(self.over_tunnel("snapshot").get("status"), 200)
        sh("ip", "link", "set", "lan1", "up", ns=PH)
        for command in ("snapshot", "refresh"):
            result = self.phone(command, "--host", LAN_SRV) if command == "snapshot" else self.phone("refresh", "--host", LAN_SRV)  # fmt: skip
            self.assertNotEqual(
                result.get("status"), 200, f"revoked device still allowed: {command}"
            )
        self.assertIn("revoke", (self.tmp / "state/gateway/audit.log").read_text())


if __name__ == "__main__":
    unittest.main()

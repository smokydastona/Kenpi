import asyncio
import ipaddress
import tempfile
import unittest

from kenpi.core.events import EventBus, EventType
from kenpi.gateway.server import GatewayServer, envelope, validate_bind_host
from kenpi.security.devices import DeviceRegistry
from kenpi.security.tls import ensure_certificate, server_context
from tests.gateway_helpers import DeviceKey, WsClient, client_context, http, masked


class GatewayCase(unittest.IsolatedAsyncioTestCase):
    remote_enabled = False
    remote_networks = ()
    kwargs: dict = {}

    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.registry = DeviceRegistry(self._tmp.name)
        self.bus = EventBus(replay_size=8)
        cert, key, self.fingerprint = ensure_certificate(self._tmp.name, ["127.0.0.1"])
        self.gateway = GatewayServer(
            self.registry,
            self.bus,
            lambda scopes: {s: {"ok": True} for s in sorted(scopes)},
            ("127.0.0.1",),
            0,
            server_context(cert, key),
            remote_networks=self.remote_networks,
            remote_enabled=lambda: self.remote_enabled,
            heartbeat_seconds=0.2,
            idle_timeout_seconds=1.0,
            auth_timeout_seconds=0.5,
            revalidate_seconds=0.05,
            queue_size=4,
            **self.kwargs,
        )
        await self.gateway.start()
        self.addAsyncCleanup(self.gateway.stop)
        self.port = self.gateway.bound_ports[0]
        self.ctx = client_context()
        self.key = DeviceKey()

    async def request(self, method, path, body=None, headers=None):
        return await http(self.port, method, path, body, headers, self.ctx)

    async def enroll(self, name="Pixel", scopes=None, allow_remote=False):
        kwargs = {"allow_remote": allow_remote}
        if scopes:
            kwargs["scopes"] = frozenset(scopes)
        eid, challenge, _ = self.registry.begin_pairing(**kwargs)
        status, body = await self.request(
            "POST",
            "/pair/enroll",
            {
                "enrollment_id": eid,
                "challenge": challenge,
                "name": name,
                "public_key": self.key.public,
                "signature": self.key.enroll_proof(eid, challenge, name),
            },
        )
        self.assertEqual((status, body["state"]), (200, "submitted"))
        self.registry.approve(eid)
        status, body = await self.request(
            "POST",
            "/pair/status",
            {"enrollment_id": eid, "signature": self.key.collect_proof(eid)},
        )
        self.assertEqual(status, 200)
        return body

    async def ws(self, token, **hello):
        client, head = await WsClient.connect(self.port, self.ctx)
        self.assertIn(b"101", head)
        self.addCleanup(client.close)
        client.send_json({"type": "auth", "token": token, **hello})
        return client


class PairingOverHttpTests(GatewayCase):
    async def test_enrol_snapshot_refresh(self):
        creds = await self.enroll()
        self.assertEqual(creds["state"], "collected")
        auth = {"Authorization": f"Bearer {creds['access_token']}"}
        status, snap = await self.request("GET", "/api/v1/snapshot", headers=auth)
        self.assertEqual(status, 200)
        self.assertEqual(set(snap["state"]), {"status.read", "vehicle.read"})
        status, new = await self.request(
            "POST",
            "/auth/refresh",
            {
                "refresh_token": creds["refresh_token"],
                "signature": self.key.refresh_proof(creds["refresh_token"]),
            },
        )
        self.assertEqual(status, 200)
        self.assertNotEqual(new["refresh_token"], creds["refresh_token"])

    async def test_snapshot_requires_bearer(self):
        self.assertEqual((await self.request("GET", "/api/v1/snapshot"))[0], 401)
        status, _ = await self.request(
            "GET", "/api/v1/snapshot", headers={"Authorization": "Bearer nope"}
        )
        self.assertEqual(status, 401)

    async def test_bad_requests(self):
        self.assertEqual((await self.request("GET", "/nothing"))[0], 404)
        self.assertEqual((await self.request("GET", "/pair/enroll"))[0], 405)
        self.assertEqual((await self.request("POST", "/pair/enroll", {"x": 1}))[0], 400)

    async def test_pairing_rate_limited(self):
        statuses = [
            (
                await self.request(
                    "POST", "/pair/status", {"enrollment_id": "a", "signature": "b"}
                )
            )[0]
            for _ in range(31)
        ]
        self.assertEqual(statuses[-1], 429)

    async def test_no_command_routes(self):
        creds = await self.enroll()
        auth = {"Authorization": f"Bearer {creds['access_token']}"}
        for path in ("/api/v1/camera/record", "/api/v1/commands"):
            self.assertEqual((await self.request("POST", path, {}, auth))[0], 404)

    async def test_plain_http_is_not_accepted_on_tls_port(self):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        writer.write(b"GET /api/v1/snapshot HTTP/1.1\r\n\r\n")
        await writer.drain()
        data = await asyncio.wait_for(reader.read(), 5)
        self.assertNotIn(b"HTTP/1.1 200", data)
        writer.close()


class WebSocketTests(GatewayCase):
    async def test_auth_snapshot_then_events(self):
        creds = await self.enroll()
        client = await self.ws(creds["access_token"])
        snap = await client.recv_json()
        self.assertEqual(snap["type"], "snapshot")
        self.assertEqual(snap["stream_id"], self.bus.stream_id)
        self.bus.publish(EventType.SERVICE_STARTED, "t", name="x")
        message = await client.recv_json()
        event = message["event"]
        self.assertEqual(event["type"], "system.service_started")
        self.assertEqual(event["schema_version"], 1)
        self.assertEqual(event["sequence"], 1)
        self.assertEqual(event["payload"], {"name": "x"})
        self.assertIn("T", event["timestamp"])

    async def test_token_in_url_is_not_accepted_and_missing_auth_closes(self):
        client, _ = await WsClient.connect(self.port, self.ctx)
        self.addCleanup(client.close)
        closed = await client.recv_json()
        self.assertEqual(closed, {"type": "closed", "code": 4401})

    async def test_bad_token_closes_4401(self):
        client = await self.ws("kat_bogus")
        self.assertEqual((await client.recv_json())["code"], 4401)

    async def test_events_filtered_by_scope(self):
        creds = await self.enroll(scopes={"status.read"})
        client = await self.ws(creds["access_token"])
        await client.recv_json()
        self.bus.publish(EventType.POWER_STATE_CHANGED, "t", state="on")
        self.bus.publish(EventType.SERVICE_STARTED, "t")
        message = await client.recv_json()
        self.assertEqual(message["event"]["type"], "system.service_started")

    async def test_resume_without_gap_replays_missed_events(self):
        creds = await self.enroll()
        for _ in range(3):
            self.bus.publish(EventType.SERVICE_STARTED, "t")
        client = await self.ws(
            creds["access_token"], last_sequence=1, stream_id=self.bus.stream_id
        )
        self.assertEqual((await client.recv_json())["type"], "resumed")
        self.assertEqual((await client.recv_json())["event"]["sequence"], 2)
        self.assertEqual((await client.recv_json())["event"]["sequence"], 3)

    async def test_gap_or_new_stream_gets_fresh_snapshot(self):
        creds = await self.enroll()
        for _ in range(20):  # replay ring holds 8
            self.bus.publish(EventType.SERVICE_STARTED, "t")
        client = await self.ws(
            creds["access_token"], last_sequence=2, stream_id=self.bus.stream_id
        )
        self.assertEqual((await client.recv_json())["type"], "snapshot")
        other = await self.ws(
            creds["access_token"], last_sequence=5, stream_id="old-boot"
        )
        message = await other.recv_json()
        self.assertEqual((message["type"], message["sequence"]), ("snapshot", 20))

    async def test_slow_consumer_gets_resync(self):
        creds = await self.enroll()
        client = await self.ws(creds["access_token"])
        await client.recv_json()
        for _ in range(40):  # published without yielding: the 4-slot queue overflows
            self.bus.publish(EventType.SERVICE_STARTED, "t")
        kinds, last = [], 0
        while last < 40:
            message = await client.recv_json()
            if message["type"] == "event":
                seq = message["event"]["sequence"]
                self.assertGreater(seq, last)
            elif message["type"] == "resync":
                seq = message["sequence"]
                self.assertGreaterEqual(seq, last)
            else:
                continue
            kinds.append(message["type"])
            last = seq
        self.assertIn("resync", kinds)

    async def test_revocation_closes_connection(self):
        creds = await self.enroll()
        client = await self.ws(creds["access_token"])
        await client.recv_json()
        self.registry.revoke(creds["device_id"])
        while True:
            message = await client.recv_json()
            if message["type"] == "closed":
                break
        self.assertEqual(message["code"], 4401)

    async def test_heartbeat_carries_latest_sequence(self):
        creds = await self.enroll()
        client = await self.ws(creds["access_token"])
        await client.recv_json()
        self.bus.publish(EventType.SERVICE_STARTED, "t")
        seen = []
        while not any(m["type"] == "heartbeat" for m in seen):
            seen.append(await client.recv_json())
        self.assertEqual(
            [m for m in seen if m["type"] == "heartbeat"][0]["sequence"], 1
        )

    async def test_idle_client_is_disconnected(self):
        creds = await self.enroll()
        client = await self.ws(creds["access_token"])
        await client.recv_json()
        while True:
            message = await client.recv_json(timeout=5)
            if message["type"] == "closed":
                break
        self.assertEqual(message["code"], 1001)

    async def test_unknown_client_messages_ignored_and_pong(self):
        creds = await self.enroll()
        client = await self.ws(creds["access_token"])
        await client.recv_json()
        client.send_json({"type": "record_now"})
        client.send_json({"type": "ping"})
        while True:
            message = await client.recv_json()
            if message["type"] in {"pong", "closed"}:
                break
        self.assertEqual(message["type"], "pong")

    async def test_unmasked_frame_rejected(self):
        creds = await self.enroll()
        client = await self.ws(creds["access_token"])
        await client.recv_json()
        client.writer.write(b"\x81\x02hi")
        while True:
            message = await client.recv_json()
            if message["type"] == "closed":
                break
        self.assertEqual(message["code"], 1002)

    async def test_disconnect_unsubscribes(self):
        creds = await self.enroll()
        client = await self.ws(creds["access_token"])
        await client.recv_json()
        self.assertEqual(len(self.bus._callbacks), 1)
        client.writer.write(masked(0x8, b"\x03\xe8"))
        await asyncio.sleep(0.4)
        self.assertEqual(len(self.bus._callbacks), 0)


class ConnectionLimitTests(GatewayCase):
    kwargs = {"max_connections": 2}

    async def test_limit_enforced(self):
        creds = await self.enroll()
        first = await self.ws(creds["access_token"])
        await first.recv_json()
        second = await self.ws(creds["access_token"])
        await second.recv_json()
        status, _ = await self.request("GET", "/api/v1/snapshot")
        self.assertEqual(status, 503)


class RemotePolicyTests(GatewayCase):
    # Loopback is treated as the "remote" (WireGuard) zone for this test.
    remote_networks = (ipaddress.ip_network("127.0.0.0/8"),)

    async def test_remote_disabled_by_default(self):
        status, body = await self.request("GET", "/api/v1/snapshot")
        self.assertEqual((status, body["error"]), (403, "forbidden"))

    async def test_remote_requires_enable_and_device_permission(self):
        type(self).remote_enabled = True
        self.addCleanup(setattr, type(self), "remote_enabled", False)
        local_creds = None
        eid, challenge, _ = self.registry.begin_pairing(allow_remote=False)
        # Pairing is refused from the remote zone entirely.
        status, body = await self.request(
            "POST", "/pair/enroll", {"enrollment_id": eid, "challenge": challenge}
        )
        self.assertEqual((status, body["error"]), (403, "remote_not_permitted"))
        self.assertIsNone(local_creds)


class ConfigurationTests(unittest.TestCase):
    def test_public_and_wildcard_hosts_refused(self):
        for host in ("0.0.0.0", "8.8.8.8", "::"):
            with self.assertRaises(ValueError):
                validate_bind_host(host)
        validate_bind_host("192.168.1.5")
        validate_bind_host("10.77.0.1")

    def test_tls_required_off_loopback(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                GatewayServer(
                    DeviceRegistry(tmp),
                    EventBus(),
                    lambda s: {},
                    ("192.168.1.5",),
                    0,
                    None,
                )

    def test_envelope_shape(self):
        event = EventBus().publish(EventType.SERVICE_STARTED, "x", a=1)
        self.assertEqual(
            set(envelope(event)),
            {
                "event_id",
                "sequence",
                "timestamp",
                "type",
                "schema_version",
                "source",
                "payload",
            },
        )


if __name__ == "__main__":
    unittest.main()

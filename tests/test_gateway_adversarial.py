import asyncio
import time

from tests.gateway_helpers import WsClient, http, masked
from tests.test_companion import PhoneGatewayTests as _Base  # noqa: F401
from tests.test_gateway import GatewayCase
from tests.test_assistant import make_assistant


async def raw(case, data: bytes, read=True):
    reader, writer = await asyncio.open_connection("127.0.0.1", case.port, ssl=case.ctx)
    writer.write(data)
    await writer.drain()
    try:
        out = await asyncio.wait_for(reader.read(), 5) if read else b""
    except (asyncio.TimeoutError, ConnectionError):
        out = b""
    writer.close()
    status = int(out.split(b" ")[1]) if out.startswith(b"HTTP/") else None
    return status, out


class Adversarial(GatewayCase):
    async def asyncSetUp(self):
        self.assistant = make_assistant()
        self.kwargs = {
            "assistant_handler": lambda d, m, p, b: self.assistant.phone_request(
                d.device_id, d.name, m, p, b
            )
        }
        await super().asyncSetUp()

    async def token(self, scopes=("status.read", "assistant.use")):
        return (await self.enroll(scopes=list(scopes)))["access_token"]

    async def test_malformed_request_lines(self):
        for data in (
            b"\r\n\r\n",
            b"GARBAGE\r\n\r\n",
            b"GET / HTTP/9.9\r\n\r\n",
            b"GET /\xff\xfe HTTP/1.1\r\n\r\n",
            b"GET  /api/v1/snapshot  HTTP/1.1 extra\r\n\r\n",
        ):
            status, _ = await raw(self, data)
            self.assertIn(status, (400, 404, 431, None), data)
        status, _ = await http(self.port, "GET", "/api/v1/snapshot", ssl_ctx=self.ctx)
        self.assertEqual(status, 401)  # still healthy

    async def test_duplicate_and_conflicting_headers_rejected(self):
        token = await self.token()
        auth = f"Authorization: Bearer {token}\r\n"
        for head in (
            f"GET /api/v1/assistant/status HTTP/1.1\r\nHost: x\r\n{auth}Authorization: Bearer bad\r\n\r\n",
            "POST /pair/status HTTP/1.1\r\nContent-Length: 2\r\nContent-Length: 50\r\n\r\n{}",
            "POST /pair/status HTTP/1.1\r\nTransfer-Encoding: chunked\r\nContent-Length: 2\r\n\r\n{}",
        ):
            status, _ = await raw(self, head.encode())
            self.assertEqual(status, 400, head)

    async def test_bad_content_length_forms(self):
        for value in ("-1", "+5", "1_0", " ", "0x10", "99999999999999999999"):
            status, _ = await raw(
                self,
                f"POST /pair/status HTTP/1.1\r\nContent-Length: {value}\r\n\r\n{{}}".encode(),
            )
            self.assertIn(status, (400, 413), value)

    async def test_oversized_bodies(self):
        status, _ = await http(
            self.port, "POST", "/pair/status", {"x": "a" * 5000}, ssl_ctx=self.ctx
        )
        self.assertEqual(status, 413)
        token = await self.token()
        status, _ = await http(
            self.port,
            "POST",
            "/api/v1/assistant/ask",
            {"text": "a" * 70000},
            {"Authorization": f"Bearer {token}"},
            self.ctx,
        )
        self.assertEqual(status, 413)

    async def test_oversized_headers(self):
        status, _ = await raw(
            self, b"GET / HTTP/1.1\r\nX: " + b"a" * 200000 + b"\r\n\r\n"
        )
        self.assertIn(status, (431, None))

    async def test_invalid_and_forged_tokens(self):
        for header in (
            "Bearer ",
            "Bearer abc",
            "Basic abc",
            "bearer",
            "Bearer " + "A" * 5000,
        ):
            status, _ = await http(
                self.port,
                "GET",
                "/api/v1/assistant/status",
                headers={"Authorization": header},
                ssl_ctx=self.ctx,
            )
            self.assertEqual(status, 401, header)

    async def test_refresh_replay_and_bad_signature(self):
        creds = await self.enroll()
        refresh = creds["refresh_token"]
        status, _ = await http(
            self.port,
            "POST",
            "/auth/refresh",
            {"refresh_token": refresh, "signature": "AAAA"},
            ssl_ctx=self.ctx,
        )
        self.assertIn(status, (400, 401))
        status, body = await http(
            self.port,
            "POST",
            "/auth/refresh",
            {"refresh_token": refresh, "signature": self.key.refresh_proof(refresh)},
            ssl_ctx=self.ctx,
        )
        self.assertEqual(status, 200)
        status, _ = await http(
            self.port,
            "POST",
            "/auth/refresh",
            {"refresh_token": refresh, "signature": self.key.refresh_proof(refresh)},
            ssl_ctx=self.ctx,
        )
        self.assertIn(status, (400, 401))  # replayed refresh token

    async def test_revoked_device_loses_http_and_websocket(self):
        creds = await self.enroll(scopes=["status.read", "assistant.use"])
        token = creds["access_token"]
        client = await self.ws(token)
        self.assertEqual((await client.recv_json())["type"], "snapshot")
        self.registry.revoke(creds["device_id"])
        status, _ = await http(
            self.port,
            "GET",
            "/api/v1/assistant/status",
            headers={"Authorization": f"Bearer {token}"},
            ssl_ctx=self.ctx,
        )
        self.assertEqual(status, 401)
        closed = None
        for _ in range(20):
            message = await client.recv_json()
            if message["type"] == "closed":
                closed = message
                break
        self.assertIsNotNone(closed)

    async def test_expired_token_rejected(self):
        token = await self.token()
        real = time.time()
        self.registry._clock = lambda: real + 10 * 3600
        status, _ = await http(
            self.port,
            "GET",
            "/api/v1/assistant/status",
            headers={"Authorization": f"Bearer {token}"},
            ssl_ctx=self.ctx,
        )
        self.assertEqual(status, 401)

    async def test_every_assistant_route_needs_auth_and_scope(self):
        no_scope = await self.token(("status.read",))
        for method, path in (
            ("GET", "/api/v1/assistant/status"),
            ("GET", "/api/v1/assistant/diagnostics"),
            ("GET", "/api/v1/assistant/logs"),
            ("POST", "/api/v1/assistant/logs"),
            ("POST", "/api/v1/assistant/ask"),
            ("GET", "/api/v1/assistant/nothing"),
            ("DELETE", "/api/v1/assistant/status"),
        ):
            status, _ = await http(
                self.port,
                method,
                path,
                {} if method == "POST" else None,
                ssl_ctx=self.ctx,
            )
            self.assertEqual(status, 401, (method, path))
            status, _ = await http(
                self.port,
                method,
                path,
                {} if method == "POST" else None,
                {"Authorization": f"Bearer {no_scope}"},
                self.ctx,
            )
            self.assertEqual(status, 403, (method, path))

    async def test_path_tricks_do_not_bypass_scope(self):
        no_scope = await self.token(("status.read",))
        for path in (
            "//api/v1/assistant/status",
            "/api/v1/assistant/../assistant/status",
            "/API/V1/ASSISTANT/status",
            "/api/v1/assistant%2fstatus",
            "/api/v1/assistant",
        ):
            status, body = await http(
                self.port,
                "GET",
                path,
                headers={"Authorization": f"Bearer {no_scope}"},
                ssl_ctx=self.ctx,
            )
            self.assertNotEqual(status, 200, path)
            self.assertNotIn("setup", body)

    async def test_no_command_routes_and_phone_cannot_approve(self):
        token = await self.token()
        auth = {"Authorization": f"Bearer {token}"}
        for path in (
            "/api/v1/assistant/approve",
            "/api/v1/commands",
            "/api/v1/camera/record",
        ):
            status, _ = await http(
                self.port,
                "POST",
                path,
                {"approval_id": "x", "approved": True},
                auth,
                self.ctx,
            )
            self.assertEqual(status, 404, path)
        status, body = await http(
            self.port,
            "POST",
            "/api/v1/assistant/ask",
            {"text": "clear the cache", "approved": True},
            auth,
            self.ctx,
        )
        self.assertEqual(status, 200)
        self.assertFalse(
            any(o["executed"] for o in body["outcomes"] if o["needs_approval"])
        )

    async def test_malformed_websocket_frames(self):
        token = await self.token()
        for frame in (
            b"\x81\x05hello",  # unmasked client frame
            b"\x03\x80\x00\x00\x00\x00",  # reserved opcode
            b"\xf1\x80\x00\x00\x00\x00",  # reserved bits set
            b"\x81\xff" + b"\xff" * 8 + b"\x00" * 4,  # absurd 64-bit length
            b"\x88\xfe\x00\x01\x00\x00\x00\x00",  # oversized control frame
        ):
            client, _ = await WsClient.connect(self.port, self.ctx)
            client.send_json({"type": "auth", "token": token})
            client.writer.write(frame)
            try:
                for _ in range(10):
                    message = await client.recv_json(2)
                    if message["type"] == "closed":
                        break
            except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError):
                pass
            client.close()
        status, _ = await http(self.port, "GET", "/api/v1/snapshot", ssl_ctx=self.ctx)
        self.assertEqual(status, 401)  # server survived

    async def test_websocket_without_auth_is_closed(self):
        client, _ = await WsClient.connect(self.port, self.ctx)
        client.writer.write(masked(0x1, b'{"type":"nope"}'))
        message = await client.recv_json(3)
        self.assertEqual(message["type"], "closed")
        client.close()

    async def test_ask_is_rate_limited(self):
        token = await self.token()
        auth = {"Authorization": f"Bearer {token}"}
        codes = set()
        for _ in range(40):
            status, _ = await http(
                self.port,
                "POST",
                "/api/v1/assistant/ask",
                {"text": "help"},
                auth,
                self.ctx,
            )
            codes.add(status)
        self.assertIn(429, codes)


if __name__ == "__main__":
    import unittest

    unittest.main()

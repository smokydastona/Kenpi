import asyncio
import socket
import unittest

from kenpi.tools.profiler import main, profile, validate_target


class ProfilerTests(unittest.IsolatedAsyncioTestCase):
    def test_rejects_public_and_non_literal_targets(self) -> None:
        for host in ("8.8.8.8", "example.com", "0.0.0.0", "224.0.0.1", "2606:4700::1111"):
            with self.assertRaises(ValueError, msg=host):
                validate_target(host)
        for host in ("192.168.1.1", "10.0.0.1", "127.0.0.1", "169.254.1.1"):
            self.assertEqual(validate_target(host), host)

    async def test_open_and_closed_without_sending_data(self) -> None:
        received: list[bytes] = []

        async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
            received.append(await reader.read(100))
            writer.close()

        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        open_port = server.sockets[0].getsockname()[1]
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            closed_port = probe.getsockname()[1]
        async with server:
            report = await profile("127.0.0.1", (open_port, closed_port), 1.0)
            await asyncio.sleep(0.1)
        states = {item["port"]: item["state"] for item in report["ports"]}
        self.assertEqual(states, {open_port: "open", closed_port: "closed"})
        self.assertEqual(received, [b""])
        self.assertEqual(report["evidence"], "E0")

    async def test_argument_validation(self) -> None:
        with self.assertRaises(ValueError):
            await profile("127.0.0.1", (0,))
        with self.assertRaises(ValueError):
            await profile("127.0.0.1", tuple(range(1, 40)))
        with self.assertRaises(ValueError):
            await profile("127.0.0.1", (80,), timeout_seconds=60)

    def test_cli_rejects_public_target(self) -> None:
        self.assertEqual(main(["--target", "8.8.8.8"]), 1)


if __name__ == "__main__":
    unittest.main()

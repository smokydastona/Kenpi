import unittest
from dataclasses import dataclass

from kenpi.camera.adapter import ConnectionState, TransportResponse
from kenpi.camera.commands import CommandDefinition, CommandRegistry
from kenpi.camera.control import CameraControlService
from kenpi.core.errors import CommandRejected
from kenpi.core.models import CommandSafety, EvidenceLevel


@dataclass
class TestAdapter:
    calls: int = 0

    async def connect(self, timeout_seconds: float) -> None:
        return None

    async def close(self) -> None:
        return None

    async def request(self, payload: bytes, timeout_seconds: float) -> TransportResponse:
        self.calls += 1
        return TransportResponse(payload, 0.0)

    async def connection_state(self) -> ConnectionState:
        return ConnectionState.CONNECTED


class TestCodec:
    protocol_version = "test-only"

    def encode_request(self, command: str, payload: bytes) -> bytes:
        return command.encode() + b":" + payload

    def decode_response(self, payload: bytes) -> bytes:
        return payload


class CameraControlTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.adapter = TestAdapter()
        definitions = (
            CommandDefinition("status", CommandSafety.READ_ONLY, EvidenceLevel.E0),
            CommandDefinition("setting", CommandSafety.REVERSIBLE_WRITE, EvidenceLevel.E0),
        )
        self.service = CameraControlService(
            self.adapter, TestCodec(), CommandRegistry(definitions)
        )

    async def test_unknown_command_is_never_transmitted(self) -> None:
        with self.assertRaises(CommandRejected):
            await self.service.execute("unlisted")
        self.assertEqual(self.adapter.calls, 0)

    async def test_write_requires_user_initiation(self) -> None:
        with self.assertRaises(CommandRejected):
            await self.service.execute("setting", b"new")
        self.assertEqual(self.adapter.calls, 0)

    async def test_response_does_not_imply_camera_ack_or_state_change(self) -> None:
        result = await self.service.execute("setting", b"new", user_initiated=True)
        self.assertTrue(result.transmitted)
        self.assertTrue(result.response_received)
        self.assertFalse(result.acknowledged)
        self.assertIsNone(result.state_verified)


if __name__ == "__main__":
    unittest.main()

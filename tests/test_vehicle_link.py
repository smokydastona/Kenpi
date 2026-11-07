import asyncio
import os
import pty
import unittest
from pathlib import Path

from kenpi.core.errors import ProtocolError
from kenpi.vehicle.link import (
    LinkState,
    SerialPort,
    VehicleCoordinator,
    VehicleLinkService,
)
from kenpi.vehicle.messages import (
    Heartbeat,
    PowerState,
    PowerStateReport,
    ShutdownAck,
    ShutdownRequest,
    from_frame_message,
    to_frame_message,
)
from kenpi.vehicle.protocol import (
    VehicleMessage,
    VehicleMessageDecoder,
    encode_message,
)

FIXTURES = Path(__file__).resolve().parent.parent / "protocol" / "vehicle_frames.txt"
TYPED = {
    "heartbeat": Heartbeat(123456),
    "power_running": PowerStateReport(PowerState.RUNNING, True),
    "power_fault_no_ignition": PowerStateReport(PowerState.FAULT, False),
    "shutdown_request": ShutdownRequest(30),
    "shutdown_ack": ShutdownAck(),
}


def _fixtures() -> list[tuple[str, int, int, bytes, bytes]]:
    rows = []
    for line in FIXTURES.read_text().splitlines():
        if line and not line.startswith("#"):
            name, kind, seq, payload, frame = line.split("|")
            rows.append(
                (
                    name,
                    int(kind),
                    int(seq),
                    bytes.fromhex(payload),
                    bytes.fromhex(frame),
                )
            )
    return rows


class SharedFixtureTests(unittest.TestCase):
    def test_fixtures_match_python_encoder_and_decoder(self) -> None:
        rows = _fixtures()
        self.assertEqual({row[0] for row in rows}, set(TYPED))
        for name, kind, seq, payload, frame in rows:
            with self.subTest(name):
                built = to_frame_message(TYPED[name], seq)
                self.assertEqual(built.message_type, kind)
                self.assertEqual(built.payload, payload)
                self.assertEqual(encode_message(built), frame)
                decoded = VehicleMessageDecoder().feed(frame)
                self.assertEqual(from_frame_message(decoded[0]), TYPED[name])


class MessageValidationTests(unittest.TestCase):
    def test_rejects_unknown_type_state_flags_and_lengths(self) -> None:
        for bad in (
            VehicleMessage(0x7F, 1),
            VehicleMessage(0x02, 1, bytes((10, 0))),
            VehicleMessage(0x02, 1, bytes((3, 2))),
            VehicleMessage(0x01, 1, b"\x00"),
            VehicleMessage(0x04, 1, b"\x00"),
        ):
            with self.subTest(bad=bad), self.assertRaises(ProtocolError):
                from_frame_message(bad)

    def test_rejects_out_of_range_fields(self) -> None:
        with self.assertRaises(ProtocolError):
            to_frame_message(Heartbeat(2**32), 1)
        with self.assertRaises(ProtocolError):
            to_frame_message(ShutdownRequest(70000), 1)


class CoordinatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = 100.0
        self.coordinator = VehicleCoordinator(5.0, lambda: self.now)

    def _frame(self, message: object, sequence: int) -> bytes:
        return encode_message(to_frame_message(message, sequence))  # type: ignore[arg-type]

    def test_link_state_follows_message_freshness(self) -> None:
        self.assertEqual(self.coordinator.snapshot().link_state, LinkState.NO_DATA)
        self.coordinator.ingest(self._frame(Heartbeat(5), 1))
        self.assertEqual(self.coordinator.snapshot().link_state, LinkState.CONNECTED)
        self.now += 5.1
        self.assertEqual(self.coordinator.snapshot().link_state, LinkState.LOST)

    def test_replayed_and_corrupt_frames_are_counted_not_applied(self) -> None:
        self.coordinator.ingest(
            self._frame(PowerStateReport(PowerState.RUNNING, True), 5)
        )
        self.coordinator.ingest(
            self._frame(PowerStateReport(PowerState.FAULT, False), 5)
        )
        corrupt = bytearray(self._frame(Heartbeat(1), 6))
        corrupt[-1] ^= 0xFF
        self.coordinator.ingest(bytes(corrupt))
        snap = self.coordinator.snapshot()
        self.assertEqual(snap.power_state, PowerState.RUNNING)
        self.assertEqual(snap.replayed_frames, 1)

    def test_unknown_message_type_counts_as_rejected(self) -> None:
        self.coordinator.ingest(encode_message(VehicleMessage(0x7F, 1)))
        self.assertEqual(self.coordinator.snapshot().rejected_frames, 1)
        self.assertEqual(self.coordinator.snapshot().link_state, LinkState.NO_DATA)

    def test_link_loss_never_requests_shutdown(self) -> None:
        self.coordinator.ingest(self._frame(Heartbeat(1), 1))
        self.now += 1000
        snap = self.coordinator.snapshot()
        self.assertEqual(snap.link_state, LinkState.LOST)
        self.assertFalse(snap.shutdown_requested)

    def test_invalid_timeout_rejected(self) -> None:
        with self.assertRaises(ValueError):
            VehicleCoordinator(0.1)


class FakeStream:
    def __init__(self) -> None:
        self.inbox: asyncio.Queue[bytes] = asyncio.Queue()
        self.written: list[bytes] = []
        self.closed = False

    async def read(self) -> bytes:
        return await self.inbox.get()

    def write(self, data: bytes) -> None:
        self.written.append(data)

    def close(self) -> None:
        self.closed = True


class LinkServiceTests(unittest.IsolatedAsyncioTestCase):
    async def _settle(self) -> None:
        for _ in range(5):
            await asyncio.sleep(0)

    async def test_acknowledges_only_after_handler_accepts(self) -> None:
        for accepted, expected_writes in ((True, 1), (False, 0), (None, 0)):
            with self.subTest(accepted=accepted):
                stream = FakeStream()
                seen: list[int] = []

                async def handler(grace: int, result=accepted) -> bool:
                    seen.append(grace)
                    return result  # type: ignore[return-value]

                service = VehicleLinkService(
                    VehicleCoordinator(), lambda s=stream: s, handler
                )
                await service.start()
                stream.inbox.put_nowait(
                    encode_message(to_frame_message(ShutdownRequest(12), 1))
                )
                await self._settle()
                self.assertEqual(seen, [12])
                self.assertEqual(len(stream.written), expected_writes)
                if expected_writes:
                    decoded = VehicleMessageDecoder().feed(stream.written[0])
                    self.assertEqual(from_frame_message(decoded[0]), ShutdownAck())
                self.assertEqual(
                    service.snapshot().shutdown_acknowledged, bool(expected_writes)
                )
                await service.stop()
                self.assertTrue(stream.closed)

    async def test_no_handler_and_failing_handler_do_not_acknowledge(self) -> None:
        async def failing(_: int) -> bool:
            raise RuntimeError("boom")

        for handler in (None, failing):
            stream = FakeStream()
            service = VehicleLinkService(
                VehicleCoordinator(), lambda s=stream: s, handler
            )
            await service.start()
            stream.inbox.put_nowait(
                encode_message(to_frame_message(ShutdownRequest(1), 1))
            )
            await self._settle()
            self.assertEqual(stream.written, [])
            await service.stop()

    async def test_double_start_and_idempotent_stop(self) -> None:
        stream = FakeStream()
        service = VehicleLinkService(VehicleCoordinator(), lambda: stream)
        await service.start()
        with self.assertRaises(RuntimeError):
            await service.start()
        await service.stop()
        await service.stop()


class SerialPortTests(unittest.IsolatedAsyncioTestCase):
    async def test_reads_frames_and_writes_ack_over_real_tty(self) -> None:
        master, slave = pty.openpty()
        path = os.ttyname(slave)
        port: SerialPort | None = None
        try:
            port = SerialPort(path, 115200)
            service = VehicleLinkService(
                VehicleCoordinator(),
                lambda: port,
                lambda grace: _accept(),  # type: ignore[arg-type,return-value]
            )
            await service.start()
            os.write(
                master,
                encode_message(
                    to_frame_message(PowerStateReport(PowerState.RUNNING, True), 1)
                )
                + encode_message(to_frame_message(ShutdownRequest(7), 2)),
            )
            ack = b""
            for _ in range(100):
                await asyncio.sleep(0.02)
                try:
                    ack += os.read(master, 64)
                except BlockingIOError:
                    pass
                if ack:
                    break
            await service.stop()
            decoded = VehicleMessageDecoder().feed(ack)
            self.assertEqual(from_frame_message(decoded[0]), ShutdownAck())
            self.assertEqual(service.snapshot().power_state, PowerState.RUNNING)
        finally:
            os.close(master)
            os.close(slave)

    async def test_rejects_unsupported_baud(self) -> None:
        with self.assertRaises(ValueError):
            SerialPort("/dev/null", 12345)


async def _accept() -> bool:
    return True

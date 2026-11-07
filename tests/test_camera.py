import asyncio
import unittest
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from kenpi.camera.adapter import ConnectionState, TransportResponse
from kenpi.camera.commands import CommandDefinition, CommandRegistry
from kenpi.camera.control import CameraControlService
from kenpi.camera.session import SessionSnapshot, SessionState
from kenpi.core.errors import CommandRejected
from kenpi.core.models import CommandSafety, EvidenceLevel


@dataclass
class TestAdapter:
    calls: int = 0

    async def connect(self, timeout_seconds: float) -> None:
        return None

    async def close(self) -> None:
        return None

    async def request(
        self, payload: bytes, timeout_seconds: float
    ) -> TransportResponse:
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


def valid_status(payload: bytes) -> bool:
    return payload == b""


def valid_setting(payload: bytes) -> bool:
    return payload == b"new"


class CameraControlTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.adapter = TestAdapter()
        definitions = (
            CommandDefinition(
                "status", CommandSafety.READ_ONLY, EvidenceLevel.E0, valid_status
            ),
            CommandDefinition(
                "setting",
                CommandSafety.REVERSIBLE_WRITE,
                EvidenceLevel.E0,
                valid_setting,
            ),
        )
        self.commands = CommandRegistry(definitions)
        self.service = CameraControlService(
            self.adapter, TestCodec(), self.commands, test_mode=True
        )

    async def test_unknown_command_is_never_transmitted(self) -> None:
        with self.assertRaises(CommandRejected):
            await self.service.execute("unlisted")
        self.assertEqual(self.adapter.calls, 0)

    async def test_unknown_and_unsupported_classifications_never_transmit(self) -> None:
        for safety in (CommandSafety.UNKNOWN, CommandSafety.UNSUPPORTED):
            service = CameraControlService(
                self.adapter,
                TestCodec(),
                CommandRegistry(
                    (
                        CommandDefinition(
                            "status", safety, EvidenceLevel.E5, valid_status
                        ),
                    )
                ),
                test_mode=True,
            )
            with self.assertRaises(CommandRejected):
                await service.execute("status", user_initiated=True)
        self.assertEqual(self.adapter.calls, 0)

    async def test_write_requires_user_initiation(self) -> None:
        with self.assertRaises(CommandRejected):
            await self.service.execute("setting", b"new")
        self.assertEqual(self.adapter.calls, 0)

    async def test_session_expiry_is_rechecked_after_encoding(self) -> None:
        class Clock(datetime):
            current = datetime.now(timezone.utc)

            @classmethod
            def now(cls, tz=None):
                return cls.current

        expires_at = Clock.fromtimestamp(
            (Clock.current + timedelta(seconds=10)).timestamp(), timezone.utc
        )

        class Provider:
            async def snapshot(self):
                return SessionSnapshot(SessionState.ACTIVE, expires_at)

        class SlowCodec(TestCodec):
            def encode_request(self, command: str, payload: bytes) -> bytes:
                Clock.current += timedelta(seconds=20)
                return super().encode_request(command, payload)

        service = CameraControlService(
            self.adapter,
            SlowCodec(),
            self.commands,
            test_mode=True,
            session_provider=Provider(),
        )
        with patch("kenpi.camera.control.datetime", Clock):
            with self.assertRaises(CommandRejected):
                await service.execute("status")
        self.assertEqual(self.adapter.calls, 0)

    async def test_queued_request_gets_fresh_session_snapshot(self) -> None:
        entered = asyncio.Event()
        release = asyncio.Event()

        class Provider:
            calls = 0

            async def snapshot(self):
                self.calls += 1
                state = SessionState.ACTIVE if self.calls == 1 else SessionState.EXPIRED
                return SessionSnapshot(state)

        class BlockingAdapter(TestAdapter):
            async def request(self, payload: bytes, timeout_seconds: float):
                self.calls += 1
                entered.set()
                await release.wait()
                return TransportResponse(payload, 0.0)

        adapter = BlockingAdapter()
        provider = Provider()
        service = CameraControlService(
            adapter,
            TestCodec(),
            self.commands,
            test_mode=True,
            session_provider=provider,
        )
        first = asyncio.create_task(service.execute("status"))
        await asyncio.wait_for(entered.wait(), 1)
        second = asyncio.create_task(service.execute("status"))
        await asyncio.sleep(0)
        self.assertEqual(provider.calls, 1)
        release.set()
        self.assertTrue((await first).response_received)
        with self.assertRaises(CommandRejected):
            await second
        self.assertEqual(provider.calls, 2)
        self.assertEqual(adapter.calls, 1)

    async def test_response_does_not_imply_camera_ack_or_state_change(self) -> None:
        result = await self.service.execute("setting", b"new", user_initiated=True)
        self.assertTrue(result.transmitted)
        self.assertTrue(result.response_received)
        self.assertFalse(result.acknowledged)
        self.assertIsNone(result.state_verified)

    async def test_production_evidence_threshold_and_test_opt_in(self) -> None:
        for evidence in EvidenceLevel:
            with self.subTest(evidence=evidence):
                commands = CommandRegistry(
                    (
                        CommandDefinition(
                            "status", CommandSafety.READ_ONLY, evidence, valid_status
                        ),
                    )
                )
                service = CameraControlService(self.adapter, TestCodec(), commands)
                if evidence < EvidenceLevel.E4:
                    with self.assertRaises(CommandRejected):
                        await service.execute("status")
                else:
                    self.assertTrue((await service.execute("status")).response_received)
        self.assertEqual(self.adapter.calls, 2)
        self.assertTrue((await self.service.execute("status")).response_received)

    async def test_stricter_production_threshold(self) -> None:
        service = CameraControlService(
            self.adapter,
            TestCodec(),
            CommandRegistry(
                (
                    CommandDefinition(
                        "status",
                        CommandSafety.READ_ONLY,
                        EvidenceLevel.E4,
                        valid_status,
                    ),
                )
            ),
            minimum_evidence=EvidenceLevel.E5,
        )
        with self.assertRaises(CommandRejected):
            await service.execute("status")
        self.assertEqual(self.adapter.calls, 0)

    async def test_payload_validation_is_operation_specific(self) -> None:
        for command, payload in (("status", b"new"), ("setting", b"")):
            with self.subTest(command=command):
                with self.assertRaises(CommandRejected):
                    await self.service.execute(command, payload, user_initiated=True)
        self.assertEqual(self.adapter.calls, 0)

    async def test_validator_must_explicitly_approve_and_not_raise(self) -> None:
        def raising_validator(payload: bytes) -> bool:
            raise RuntimeError("not valid")

        for validator in (
            None,
            lambda payload: False,
            lambda payload: 1,
            lambda payload: "yes",
            raising_validator,
        ):
            with self.subTest(validator=validator):
                service = CameraControlService(
                    self.adapter,
                    TestCodec(),
                    CommandRegistry(
                        (
                            CommandDefinition(
                                "status",
                                CommandSafety.READ_ONLY,
                                EvidenceLevel.E4,
                                validator,
                            ),
                        )
                    ),
                )
                with self.assertRaises(CommandRejected):
                    await service.execute("status")
        self.assertEqual(self.adapter.calls, 0)

    async def test_runtime_inputs_fail_closed(self) -> None:
        for command in (None, [], 1, b"status"):
            with self.subTest(command=command):
                with self.assertRaises(CommandRejected):
                    await self.service.execute(command)
        for payload in (None, "", bytearray(), memoryview(b"")):
            with self.subTest(payload=payload):
                with self.assertRaises(CommandRejected):
                    await self.service.execute("status", payload)
        for initiated in (1, "true", None):
            with self.subTest(initiated=initiated):
                with self.assertRaises(CommandRejected):
                    await self.service.execute(
                        "setting", b"new", user_initiated=initiated
                    )
        self.assertEqual(self.adapter.calls, 0)

    async def test_encode_failures_never_transmit(self) -> None:
        class BadCodec(TestCodec):
            def encode_request(self, command: str, payload: bytes) -> bytes:
                raise ValueError("bad encoding")

        class WrongTypeCodec(TestCodec):
            def encode_request(self, command: str, payload: bytes) -> bytes:
                return "not bytes"

        for codec in (BadCodec(), WrongTypeCodec()):
            service = CameraControlService(
                self.adapter, codec, self.commands, test_mode=True
            )
            result = await service.execute("status")
            self.assertFalse(result.request_created)
            self.assertFalse(result.transmitted)
            self.assertIn("could not be encoded", result.error)
        self.assertEqual(self.adapter.calls, 0)

    async def test_decode_failures_preserve_only_observed_milestones(self) -> None:
        class BadCodec(TestCodec):
            def decode_response(self, payload: bytes) -> bytes:
                raise ValueError("bad decoding")

        class WrongTypeCodec(TestCodec):
            def decode_response(self, payload: bytes) -> bytes:
                return None

        for codec in (BadCodec(), WrongTypeCodec()):
            service = CameraControlService(
                self.adapter, codec, self.commands, test_mode=True
            )
            result = await service.execute("status")
            self.assertTrue(result.request_created)
            self.assertTrue(result.transmitted)
            self.assertTrue(result.response_received)
            self.assertFalse(result.acknowledged)
            self.assertIsNone(result.state_verified)
            self.assertIsNone(result.value)
            self.assertIn("could not be decoded", result.error)

    async def test_malformed_transport_response_fails_closed(self) -> None:
        for response in (None, b"bytes", TransportResponse("text", 0.0)):

            class MalformedAdapter(TestAdapter):
                async def request(self, payload: bytes, timeout_seconds: float):
                    self.calls += 1
                    return response

            adapter = MalformedAdapter()
            service = CameraControlService(
                adapter, TestCodec(), self.commands, test_mode=True
            )
            result = await service.execute("status")
            self.assertIsNone(result.value)
            self.assertFalse(result.acknowledged)
            self.assertIsNone(result.state_verified)
            self.assertIn("could not be decoded", result.error)
            self.assertEqual(adapter.calls, 1)

    async def test_transport_failure_does_not_infer_transmission_or_retry(self) -> None:
        class FailingAdapter(TestAdapter):
            async def request(self, payload: bytes, timeout_seconds: float):
                self.calls += 1
                raise OSError("unavailable")

        adapter = FailingAdapter()
        service = CameraControlService(
            adapter, TestCodec(), self.commands, test_mode=True
        )
        result = await service.execute("status")
        self.assertTrue(result.request_created)
        self.assertFalse(result.transmitted)
        self.assertFalse(result.response_received)
        self.assertFalse(result.acknowledged)
        self.assertIsNone(result.state_verified)
        self.assertIn("transport failed", result.error)
        self.assertEqual(adapter.calls, 1)

    async def test_timeout_cancels_transport_without_retry(self) -> None:
        cancelled = asyncio.Event()

        class HangingAdapter(TestAdapter):
            async def request(self, payload: bytes, timeout_seconds: float):
                self.calls += 1
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()

        adapter = HangingAdapter()
        service = CameraControlService(
            adapter, TestCodec(), self.commands, 0.1, test_mode=True
        )
        result = await service.execute("status")
        self.assertTrue(cancelled.is_set())
        self.assertTrue(result.request_created)
        self.assertFalse(result.transmitted)
        self.assertFalse(result.response_received)
        self.assertEqual(result.error, "camera request timed out")
        self.assertEqual(adapter.calls, 1)

    async def test_requests_are_serialized_and_cancellation_releases_lock(self) -> None:
        entered = asyncio.Event()
        release = asyncio.Event()

        class BlockingAdapter(TestAdapter):
            active = 0
            maximum_active = 0

            async def request(self, payload: bytes, timeout_seconds: float):
                self.calls += 1
                self.active += 1
                self.maximum_active = max(self.maximum_active, self.active)
                entered.set()
                try:
                    await release.wait()
                    return TransportResponse(payload, 0.0)
                finally:
                    self.active -= 1

        adapter = BlockingAdapter()
        service = CameraControlService(
            adapter, TestCodec(), self.commands, test_mode=True
        )
        first = asyncio.create_task(service.execute("status"))
        await asyncio.wait_for(entered.wait(), 1)
        second = asyncio.create_task(service.execute("status"))
        await asyncio.sleep(0)
        self.assertEqual(adapter.calls, 1)
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        release.set()
        self.assertTrue((await second).response_received)
        self.assertEqual(adapter.calls, 2)
        self.assertEqual(adapter.maximum_active, 1)

    async def test_cancelling_queued_request_never_transmits(self) -> None:
        entered = asyncio.Event()
        release = asyncio.Event()

        class BlockingAdapter(TestAdapter):
            async def request(self, payload: bytes, timeout_seconds: float):
                self.calls += 1
                entered.set()
                await release.wait()
                return TransportResponse(payload, 0.0)

        adapter = BlockingAdapter()
        service = CameraControlService(
            adapter, TestCodec(), self.commands, test_mode=True
        )
        first = asyncio.create_task(service.execute("status"))
        await asyncio.wait_for(entered.wait(), 1)
        queued = asyncio.create_task(service.execute("status"))
        await asyncio.sleep(0)
        queued.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await queued
        release.set()
        await first
        self.assertEqual(adapter.calls, 1)

    async def test_session_snapshot_rejects_inactive_expired_or_malformed(self) -> None:
        now = datetime.now(timezone.utc)
        snapshots = [
            SessionSnapshot(state)
            for state in SessionState
            if state is not SessionState.ACTIVE
        ] + [
            SessionSnapshot(SessionState.ACTIVE, now - timedelta(seconds=1)),
            SessionSnapshot(SessionState.ACTIVE, datetime.now()),
            SessionSnapshot(SessionState.ACTIVE, "tomorrow"),
            SessionSnapshot("active"),
            None,
        ]
        for snapshot in snapshots:

            class Provider:
                async def snapshot(self):
                    return snapshot

                async def authenticate(self, timeout_seconds):
                    raise AssertionError("must not authenticate")

            service = CameraControlService(
                self.adapter,
                TestCodec(),
                self.commands,
                test_mode=True,
                session_provider=Provider(),
            )
            with self.assertRaises(CommandRejected):
                await service.execute("status")
        self.assertEqual(self.adapter.calls, 0)

    async def test_active_session_is_allowed_without_authentication(self) -> None:
        class Provider:
            async def snapshot(self):
                return SessionSnapshot(
                    SessionState.ACTIVE,
                    datetime.now(timezone.utc) + timedelta(seconds=60),
                )

        service = CameraControlService(
            self.adapter,
            TestCodec(),
            self.commands,
            test_mode=True,
            session_provider=Provider(),
        )
        self.assertTrue((await service.execute("status")).response_received)
        self.assertEqual(self.adapter.calls, 1)

    async def test_snapshot_timeout_failure_and_cancellation_fail_closed(self) -> None:
        cancelled = asyncio.Event()
        entered = asyncio.Event()

        class Provider:
            async def snapshot(self):
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()

        service = CameraControlService(
            self.adapter,
            TestCodec(),
            self.commands,
            0.1,
            test_mode=True,
            session_provider=Provider(),
        )
        with self.assertRaises(CommandRejected):
            await service.execute("status")
        self.assertTrue(cancelled.is_set())
        cancelled.clear()
        entered.clear()
        task = asyncio.create_task(service.execute("status"))
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(cancelled.is_set())

        class FailingProvider:
            async def snapshot(self):
                raise OSError("unavailable")

        service = CameraControlService(
            self.adapter,
            TestCodec(),
            self.commands,
            test_mode=True,
            session_provider=FailingProvider(),
        )
        with self.assertRaises(CommandRejected):
            await service.execute("status")
        self.assertEqual(self.adapter.calls, 0)


class CommandConfigurationTests(unittest.TestCase):
    def test_definition_types_and_duplicate_names_are_rejected(self) -> None:
        for name, safety, evidence, validator in (
            (None, CommandSafety.READ_ONLY, EvidenceLevel.E4, valid_status),
            (" ", CommandSafety.READ_ONLY, EvidenceLevel.E4, valid_status),
            ("status", "read_only", EvidenceLevel.E4, valid_status),
            ("status", CommandSafety.READ_ONLY, 4, valid_status),
            ("status", CommandSafety.READ_ONLY, EvidenceLevel.E4, "validator"),
        ):
            with self.subTest(name=name, safety=safety, evidence=evidence):
                with self.assertRaises(ValueError):
                    CommandDefinition(name, safety, evidence, validator)
        definition = CommandDefinition(
            "status", CommandSafety.READ_ONLY, EvidenceLevel.E4, valid_status
        )
        with self.assertRaises(ValueError):
            CommandRegistry((definition, definition))
        with self.assertRaises(ValueError):
            CommandRegistry(("status",))

    def test_configuration_cannot_weaken_production_or_use_truthy_test_mode(
        self,
    ) -> None:
        for threshold in (EvidenceLevel.E0, EvidenceLevel.E3, 4, "E4", None):
            with self.subTest(threshold=threshold):
                with self.assertRaises(ValueError):
                    CameraControlService(
                        TestAdapter(),
                        TestCodec(),
                        CommandRegistry(),
                        minimum_evidence=threshold,
                    )
        for timeout in (True, None, "3", float("nan"), float("inf"), 0.09, 61):
            with self.subTest(timeout=timeout):
                with self.assertRaises(ValueError):
                    CameraControlService(
                        TestAdapter(), TestCodec(), CommandRegistry(), timeout
                    )
        for test_mode in (1, "yes", None):
            with self.subTest(test_mode=test_mode):
                with self.assertRaises(ValueError):
                    CameraControlService(
                        TestAdapter(),
                        TestCodec(),
                        CommandRegistry(),
                        test_mode=test_mode,
                    )


if __name__ == "__main__":
    unittest.main()

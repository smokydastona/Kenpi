import asyncio
import unittest

from kenpi.services.coordinator import ServiceCoordinator


class RecordingService:
    def __init__(self, name: str, events: list[str], fail_start: bool = False) -> None:
        self.name = name
        self.events = events
        self.fail_start = fail_start

    async def start(self) -> None:
        self.events.append(f"start:{self.name}")
        if self.fail_start:
            raise RuntimeError(self.name)

    async def stop(self) -> None:
        self.events.append(f"stop:{self.name}")


class CoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_failure_stops_started_services_in_reverse_order(self) -> None:
        events: list[str] = []
        coordinator = ServiceCoordinator(
            (
                RecordingService("camera", events),
                RecordingService("api", events, fail_start=True),
            )
        )
        with self.assertRaisesRegex(RuntimeError, "api"):
            await coordinator.start()
        self.assertEqual(
            events, ["start:camera", "start:api", "stop:api", "stop:camera"]
        )

    async def test_cleanup_failures_preserve_startup_failure(self) -> None:
        events: list[str] = []

        class FailingCleanup(RecordingService):
            async def stop(self) -> None:
                await super().stop()
                raise ValueError("cleanup")

        coordinator = ServiceCoordinator(
            (
                RecordingService("camera", events),
                FailingCleanup("api", events, fail_start=True),
            )
        )
        with self.assertRaises(ExceptionGroup) as caught:
            await coordinator.start()
        self.assertEqual(str(caught.exception.exceptions[0]), "api")
        cleanup = caught.exception.exceptions[1]
        self.assertIsInstance(cleanup, ExceptionGroup)
        self.assertEqual(str(cleanup.exceptions[0]), "cleanup")
        self.assertEqual(events[-2:], ["stop:api", "stop:camera"])
        await coordinator.stop()

    async def test_cancelled_partial_startup_rolls_back(self) -> None:
        events: list[str] = []
        entered = asyncio.Event()

        class BlockingService(RecordingService):
            async def start(self) -> None:
                await super().start()
                entered.set()
                await asyncio.Event().wait()

        coordinator = ServiceCoordinator(
            (RecordingService("camera", events), BlockingService("api", events))
        )
        task = asyncio.create_task(coordinator.start())
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(events[-2:], ["stop:api", "stop:camera"])

    async def test_cancellation_during_stop_finishes_all_cleanup(self) -> None:
        events: list[str] = []
        entered = asyncio.Event()
        release = asyncio.Event()

        class SlowStop(RecordingService):
            async def stop(self) -> None:
                entered.set()
                await release.wait()
                await super().stop()

        coordinator = ServiceCoordinator(
            (RecordingService("camera", events), SlowStop("api", events))
        )
        await coordinator.start()
        task = asyncio.create_task(coordinator.stop())
        await entered.wait()
        task.cancel()
        release.set()
        with self.assertRaises(BaseExceptionGroup) as caught:
            await task
        self.assertIsInstance(caught.exception.exceptions[0], asyncio.CancelledError)
        self.assertEqual(events[-2:], ["stop:api", "stop:camera"])
        await coordinator.stop()

    async def test_start_stop_and_restart(self) -> None:
        events: list[str] = []
        coordinator = ServiceCoordinator((RecordingService("api", events),))
        await coordinator.start()
        with self.assertRaises(RuntimeError):
            await coordinator.start()
        await coordinator.stop()
        await coordinator.stop()
        await coordinator.start()
        await coordinator.stop()
        self.assertEqual(events, ["start:api", "stop:api"] * 2)


if __name__ == "__main__":
    unittest.main()

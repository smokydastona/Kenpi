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
        self.assertEqual(events, ["start:camera", "start:api", "stop:camera"])


if __name__ == "__main__":
    unittest.main()

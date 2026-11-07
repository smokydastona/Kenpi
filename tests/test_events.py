import asyncio
import json
import unittest
import urllib.request

from kenpi.api.app import KenpiApiService
from kenpi.core.events import EventBus, EventType
from kenpi.services.coordinator import ServiceCoordinator


class EventBusTests(unittest.TestCase):
    def test_sequence_is_monotonic_and_ids_unique(self):
        bus = EventBus(clock=lambda: 5.0)
        a = bus.publish(EventType.SERVICE_STARTED, "x")
        b = bus.publish(EventType.SERVICE_STOPPED, "x", reason="done")
        self.assertEqual((a.seq, b.seq), (1, 2))
        self.assertNotEqual(a.id, b.id)
        self.assertEqual(b.data, {"reason": "done"})

    def test_replay_ring_is_bounded_and_reports_gap(self):
        bus = EventBus(replay_size=3)
        for _ in range(5):
            bus.publish(EventType.SERVICE_STARTED, "x")
        replay = bus.events_after(0)
        self.assertEqual([e["seq"] for e in replay["events"]], [3, 4, 5])
        self.assertTrue(replay["gap"])
        self.assertFalse(bus.events_after(2)["gap"])
        self.assertEqual(bus.events_after(3, limit=1)["events"][0]["seq"], 4)

    def test_callback_failure_is_isolated(self):
        bus = EventBus()
        seen = []
        bus.subscribe(lambda e: 1 / 0)
        bus.subscribe(seen.append)
        with self.assertLogs("kenpi.core.events", "ERROR"):
            bus.publish(EventType.SERVICE_STARTED, "x")
        self.assertEqual(len(seen), 1)
        self.assertEqual(bus.callback_failures, 1)

    def test_filter_and_unsubscribe(self):
        bus = EventBus()
        seen = []
        off = bus.subscribe(seen.append, frozenset({EventType.SERVICE_FAILED}))
        bus.publish(EventType.SERVICE_STARTED, "x")
        bus.publish(EventType.SERVICE_FAILED, "x")
        off()
        bus.publish(EventType.SERVICE_FAILED, "x")
        self.assertEqual([e.type for e in seen], [EventType.SERVICE_FAILED])

    def test_queue_drops_oldest_and_counts(self):
        bus = EventBus()
        queue, off = bus.subscribe_queue(2)
        for _ in range(5):
            bus.publish(EventType.SERVICE_STARTED, "x")
        self.assertEqual([e.seq for e in queue.drain()], [4, 5])
        self.assertEqual(queue.dropped, 3)
        off()
        bus.publish(EventType.SERVICE_STARTED, "x")
        self.assertEqual(queue.drain(), [])

    def test_closed_bus_publishes_nothing(self):
        bus = EventBus()
        bus.close()
        self.assertIsNone(bus.publish(EventType.SERVICE_STARTED, "x"))

    def test_invalid_sizes_rejected(self):
        with self.assertRaises(ValueError):
            EventBus(replay_size=0)
        with self.assertRaises(ValueError):
            EventBus().subscribe_queue(0)


class _Service:
    def __init__(self, fail=False):
        self.fail = fail

    async def start(self):
        if self.fail:
            raise OSError("boom")

    async def stop(self):
        pass


class CoordinatorEventTests(unittest.IsolatedAsyncioTestCase):
    async def test_lifecycle_events(self):
        bus = EventBus()
        coordinator = ServiceCoordinator((_Service(),), events=bus)
        await coordinator.start()
        await coordinator.stop()
        types = [e["type"] for e in bus.events_after(0)["events"]]
        self.assertEqual(types, ["system.service_started", "system.service_stopped"])

    async def test_start_failure_event_and_rollback(self):
        bus = EventBus()
        coordinator = ServiceCoordinator((_Service(), _Service(fail=True)), events=bus)
        with self.assertRaises(OSError):
            await coordinator.start()
        types = [e["type"] for e in bus.events_after(0)["events"]]
        self.assertIn("system.service_failed", types)
        self.assertEqual(types.count("system.service_stopped"), 2)


class EventsRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bus = EventBus()
        self.bus.publish(EventType.SAFE_MODE_ENTERED, "test")
        self.service = KenpiApiService(port=0, events=self.bus)
        await self.service.start()
        self.base = f"http://127.0.0.1:{self.service.bound_port}"

    async def asyncTearDown(self):
        await self.service.stop()

    def get(self, path):
        def call():
            try:
                with urllib.request.urlopen(self.base + path, timeout=5) as r:
                    return r.status, json.loads(r.read())
            except urllib.error.HTTPError as exc:
                return exc.code, json.loads(exc.read())

        return asyncio.to_thread(call)

    async def test_events_route(self):
        status, body = await self.get("/api/v1/events?after=0&limit=10")
        self.assertEqual(status, 200)
        self.assertEqual(body["events"][0]["type"], "system.safe_mode_entered")
        status, body = await self.get("/api/v1/events?after=1")
        self.assertEqual(body["events"], [])

    async def test_bad_query_rejected(self):
        for q in ("after=-1", "limit=0", "limit=9999", "after=x"):
            status, _ = await self.get(f"/api/v1/events?{q}")
            self.assertEqual(status, 400, q)

    async def test_unavailable_without_bus(self):
        service = KenpiApiService(port=0)
        await service.start()
        try:

            def call():
                try:
                    urllib.request.urlopen(
                        f"http://127.0.0.1:{service.bound_port}/api/v1/events",
                        timeout=5,
                    )
                except urllib.error.HTTPError as exc:
                    return exc.code

            self.assertEqual(await asyncio.to_thread(call), 503)
        finally:
            await service.stop()


if __name__ == "__main__":
    unittest.main()

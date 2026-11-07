import asyncio
import unittest

from kenpi.video.buffer import BoundedFrameBuffer, MetricsTracker
from kenpi.video.pipeline import VideoFrame


def frame(n: int = 1, received: float = 0.0) -> VideoFrame:
    return VideoFrame(bytes([n]), 640, 360, received_monotonic=received)


class BufferTests(unittest.IsolatedAsyncioTestCase):
    async def test_drops_oldest_when_full(self) -> None:
        buffer = BoundedFrameBuffer(2)
        for n in (1, 2, 3):
            buffer.put(frame(n))
        self.assertEqual(buffer.dropped, 1)
        self.assertEqual((await buffer.get()).data, b"\x02")
        self.assertEqual((await buffer.get()).data, b"\x03")

    async def test_get_waits_then_receives(self) -> None:
        buffer = BoundedFrameBuffer()
        task = asyncio.create_task(buffer.get())
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        buffer.put(frame(7))
        self.assertEqual((await task).data, b"\x07")

    async def test_close_drains_then_returns_none_and_rejects_put(self) -> None:
        buffer = BoundedFrameBuffer()
        buffer.put(frame(1))
        buffer.close()
        self.assertIsNotNone(await buffer.get())
        self.assertIsNone(await buffer.get())
        with self.assertRaises(RuntimeError):
            buffer.put(frame(2))

    async def test_close_wakes_waiting_reader(self) -> None:
        buffer = BoundedFrameBuffer()
        task = asyncio.create_task(buffer.get())
        await asyncio.sleep(0)
        buffer.close()
        self.assertIsNone(await task)

    def test_capacity_validated(self) -> None:
        for bad in (0, 65, True, 1.5):
            with self.assertRaises(ValueError):
                BoundedFrameBuffer(bad)  # type: ignore[arg-type]


class MetricsTests(unittest.TestCase):
    def test_fps_latency_resolution_from_real_arrivals(self) -> None:
        now = [10.0]
        tracker = MetricsTracker(5.0, lambda: now[0])
        self.assertTrue(tracker.is_stalled(1))
        self.assertIsNone(tracker.metrics().frames_per_second)
        for i in range(11):
            now[0] = 10.0 + i * 0.1
            tracker.record(frame(1, received=now[0] - 0.05))
        metrics = tracker.metrics()
        self.assertAlmostEqual(metrics.frames_per_second, 10.0, places=3)
        self.assertAlmostEqual(metrics.latency_ms, 50.0, places=3)
        self.assertEqual(metrics.resolution, (640, 360))
        self.assertFalse(tracker.is_stalled(1))
        now[0] += 2
        self.assertTrue(tracker.is_stalled(1))

    def test_window_expires_old_frames(self) -> None:
        now = [0.0]
        tracker = MetricsTracker(1.0, lambda: now[0])
        tracker.record(frame())
        now[0] = 0.5
        tracker.record(frame())
        now[0] = 10.0
        self.assertIsNone(tracker.metrics().frames_per_second)

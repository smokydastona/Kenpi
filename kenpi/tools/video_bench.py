"""Reproducible benchmarks for the video pipeline, for use on the target Pi.

Two independent measurements, both reported with the host they ran on:

* ``pipeline``: synthetic frames through BoundedFrameBuffer and MetricsTracker. This measures
  Kenpi's own buffering and bookkeeping (throughput, drops, peak Python memory, CPU time).
  It involves no codec and no camera, so it says nothing about A810 Lite video.
* ``decode``: software decode of a caller-supplied video file through ffmpeg, to size the Pi's
  decode headroom. It runs only when ffmpeg is installed and reports "unavailable" otherwise.

Neither result is evidence for Gate 2, which needs real A810 Lite frames.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import shutil
import subprocess
import sys
import time
import tracemalloc
from pathlib import Path
from typing import Any

from kenpi.video.buffer import BoundedFrameBuffer, MetricsTracker
from kenpi.video.pipeline import VideoFrame


async def bench_pipeline(
    frames: int, width: int, height: int, capacity: int, consumer_delay_ms: float
) -> dict[str, Any]:
    if not (1 <= frames <= 1_000_000 and 16 <= width <= 7680 and 16 <= height <= 4320):
        raise ValueError("frames, width or height out of range")
    if consumer_delay_ms < 0:
        raise ValueError("consumer delay must be non-negative")
    buffer = BoundedFrameBuffer(capacity)
    tracker = MetricsTracker(window_seconds=60.0)
    payload = bytes(
        width * height * 3 // 2
    )  # one shared I420-sized buffer, no per-frame copy
    delivered = 0

    async def consume() -> None:
        nonlocal delivered
        while (frame := await buffer.get()) is not None:
            tracker.record(frame)
            delivered += 1
            await asyncio.sleep(consumer_delay_ms / 1000.0)

    tracemalloc.start()
    cpu_start, wall_start = time.process_time(), time.monotonic()
    consumer = asyncio.create_task(consume())
    for index in range(frames):
        buffer.put(VideoFrame(payload, width, height, index / 30.0, time.monotonic()))
        await asyncio.sleep(0)
    buffer.close()
    await consumer
    wall, cpu = time.monotonic() - wall_start, time.process_time() - cpu_start
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {
        "frames_offered": frames,
        "frames_delivered": delivered,
        "frames_dropped": buffer.dropped,
        "wall_seconds": round(wall, 4),
        "cpu_seconds": round(cpu, 4),
        "offered_per_second": round(frames / wall, 1) if wall else None,
        "peak_python_bytes": peak,
        "frame_bytes": len(payload),
        "capacity": capacity,
        "accounting_ok": delivered + buffer.dropped == frames,
    }


def bench_decode(path: Path, timeout: float = 300.0) -> dict[str, Any]:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return {"status": "unavailable", "reason": "ffmpeg is not installed"}
    if not path.is_file():
        raise ValueError("input file not found")
    started = time.monotonic()
    done = subprocess.run(
        [ffmpeg, "-v", "error", "-benchmark", "-i", str(path), "-f", "null", "-"],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    return {
        "status": "ok" if done.returncode == 0 else "failed",
        "exit_code": done.returncode,
        "wall_seconds": round(time.monotonic() - started, 3),
        "ffmpeg_benchmark": [
            line.strip()
            for line in done.stderr.splitlines()
            if line.startswith("bench")
        ],
        "error": done.stderr.strip()[-500:] if done.returncode else None,
    }


def host_info() -> dict[str, str]:
    model = ""
    try:
        model = (
            Path("/proc/device-tree/model")
            .read_bytes()
            .split(b"\0")[0]
            .decode("ascii", "replace")
        )
    except OSError:
        pass
    return {
        "machine": platform.machine(),
        "python": platform.python_version(),
        "board_model": model or "unknown",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Kenpi video benchmarks")
    sub = parser.add_subparsers(dest="mode", required=True)
    p = sub.add_parser("pipeline")
    p.add_argument("--frames", type=int, default=3000)
    p.add_argument("--width", type=int, default=1920)
    p.add_argument("--height", type=int, default=1080)
    p.add_argument("--capacity", type=int, default=4)
    p.add_argument("--consumer-delay-ms", type=float, default=0.0)
    d = sub.add_parser("decode")
    d.add_argument("file", type=Path)
    arguments = parser.parse_args(argv)
    try:
        if arguments.mode == "pipeline":
            result = asyncio.run(
                bench_pipeline(
                    arguments.frames,
                    arguments.width,
                    arguments.height,
                    arguments.capacity,
                    arguments.consumer_delay_ms,
                )
            )
        else:
            result = bench_decode(arguments.file)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {"host": host_info(), "mode": arguments.mode, "result": result}, indent=2
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

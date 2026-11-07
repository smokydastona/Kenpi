"""Explicit lifecycle coordination for independent services."""

from __future__ import annotations

import asyncio
from typing import Protocol


class ManagedService(Protocol):
    """stop must be safe after partial startup and when already stopped."""

    async def start(self) -> None: ...

    async def stop(self) -> None: ...


class ServiceCoordinator:
    def __init__(self, services: tuple[ManagedService, ...]) -> None:
        self._services = services
        self._started: list[ManagedService] = []
        self._lock = asyncio.Lock()

    def describe(self) -> dict[str, object]:
        """Best-effort, lock-free snapshot of coordinator state for diagnostics.

        This intentionally does not take ``self._lock``: it must never block or interfere
        with service startup/shutdown, and callers (e.g. an HTTP status endpoint running on
        a worker thread) must treat the result as a point-in-time snapshot that may be
        slightly stale or, in rare interleavings, reflect a state that is already changing.
        """
        started = list(self._started)
        return {
            "configured_service_count": len(self._services),
            "configured_services": [
                type(service).__name__ for service in self._services
            ],
            "started_service_count": len(started),
            "started_services": [type(service).__name__ for service in started],
            "is_running": bool(started),
        }

    async def start(self) -> None:
        async with self._lock:
            if self._started:
                raise RuntimeError("services are already started")
            try:
                for service in self._services:
                    self._started.append(service)
                    await service.start()
            except BaseException as startup_error:
                try:
                    await self._stop()
                except BaseException as cleanup_error:
                    raise BaseExceptionGroup(
                        "service startup and rollback failed",
                        [startup_error, cleanup_error],
                    ) from None
                raise

    async def stop(self) -> None:
        async with self._lock:
            await self._stop()

    async def _stop(self) -> None:
        failures: list[BaseException] = []
        while self._started:
            service = self._started.pop()
            task = asyncio.create_task(service.stop())
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError as exc:
                    if not task.cancelled():
                        failures.append(exc)
                except BaseException:
                    break
            try:
                task.result()
            except BaseException as exc:
                failures.append(exc)
        if failures:
            raise BaseExceptionGroup("one or more services failed to stop", failures)

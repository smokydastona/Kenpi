"""Explicit lifecycle coordination for independent services."""

from __future__ import annotations

from typing import Protocol


class ManagedService(Protocol):
    async def start(self) -> None: ...

    async def stop(self) -> None: ...


class ServiceCoordinator:
    def __init__(self, services: tuple[ManagedService, ...]) -> None:
        self._services = services
        self._started: list[ManagedService] = []

    async def start(self) -> None:
        if self._started:
            raise RuntimeError("services are already started")
        try:
            for service in self._services:
                await service.start()
                self._started.append(service)
        except Exception:
            await self.stop()
            raise

    async def stop(self) -> None:
        failures: list[Exception] = []
        while self._started:
            service = self._started.pop()
            try:
                await service.stop()
            except Exception as exc:
                failures.append(exc)
        if failures:
            raise ExceptionGroup("one or more services failed to stop", failures)

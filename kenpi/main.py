"""Command-line entry point for the read-only local status service."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from pathlib import Path

from kenpi.api.app import KenpiApiService
from kenpi.core.config import load_config
from kenpi.media.library import LocalMediaLibrary
from kenpi.services.coordinator import ServiceCoordinator
from kenpi.vehicle.link import SerialPort, VehicleCoordinator, VehicleLinkService

logger = logging.getLogger(__name__)


async def _serve(config_path: Path) -> None:
    config = load_config(config_path)
    # The API service needs a reference to the coordinator for its status endpoint, but the
    # coordinator needs the already-constructed service. Resolve the cycle with a one-element
    # list that is filled immediately after construction, before the service can ever be
    # started (and therefore before any request can reach status_provider).
    coordinator_ref: list[ServiceCoordinator] = []
    link: VehicleLinkService | None = None
    if config.vehicle_uart_enabled:
        device, baud = config.vehicle_uart_device, config.vehicle_uart_baud
        link = VehicleLinkService(
            VehicleCoordinator(config.vehicle_heartbeat_timeout_seconds),
            lambda: SerialPort(device, baud),
        )
    library: LocalMediaLibrary | None = None
    if config.media_directory:
        library = LocalMediaLibrary(config.media_directory)
        removed = library.recover_interrupted_transfers()
        if removed:
            logger.warning("Removed %d interrupted partial downloads", len(removed))
    service = KenpiApiService(
        config.api_host,
        config.api_port,
        head_unit=config.head_unit,
        status_provider=lambda: coordinator_ref[0].describe(),
        media_library=library,
        vehicle_provider=(lambda: link.snapshot().describe()) if link else None,
    )
    # The link starts first so the API never reports a link that is not running.
    coordinator = ServiceCoordinator((link, service) if link else (service,))
    coordinator_ref.append(coordinator)
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    installed_signals: list[signal.Signals] = []
    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(signum, stopped.set)
            installed_signals.append(signum)
        await coordinator.start()
        logger.info("Local status service started")
        await stopped.wait()
    finally:
        try:
            await coordinator.stop()
            logger.info("Local status service stopped")
        finally:
            for signum in installed_signals:
                loop.remove_signal_handler(signum)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Kenpi local status dashboard")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/kenpi.example.toml"),
        help="TOML configuration path",
    )
    arguments = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(_serve(arguments.config))


if __name__ == "__main__":
    main()

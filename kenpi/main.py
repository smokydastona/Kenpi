"""Command-line entry point for the read-only local status service."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from pathlib import Path

from kenpi.api.app import KenpiApiService
from kenpi.core.config import load_config
from kenpi.services.coordinator import ServiceCoordinator

logger = logging.getLogger(__name__)


async def _serve(config_path: Path) -> None:
    config = load_config(config_path)
    service = KenpiApiService(config.api_host, config.api_port)
    coordinator = ServiceCoordinator((service,))
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

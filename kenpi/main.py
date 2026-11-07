"""Command-line entry point for the read-only local status service."""

from __future__ import annotations

import argparse
import asyncio
import signal
from pathlib import Path

from kenpi.api.app import KenpiApiService
from kenpi.core.config import load_config


async def _serve(config_path: Path) -> None:
    config = load_config(config_path)
    service = KenpiApiService(config.api_host, config.api_port)
    await service.start()
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stopped.set)
    try:
        await stopped.wait()
    finally:
        await service.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Kenpi local status dashboard")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/kenpi.example.toml"),
        help="TOML configuration path",
    )
    arguments = parser.parse_args()
    asyncio.run(_serve(arguments.config))


if __name__ == "__main__":
    main()

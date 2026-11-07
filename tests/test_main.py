import asyncio
import signal
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from kenpi.core.config import KenpiConfig
from kenpi.main import _serve


class MainTests(unittest.IsolatedAsyncioTestCase):
    async def test_signal_stops_service_and_removes_handlers(self) -> None:
        handlers: dict[signal.Signals, object] = {}
        service = AsyncMock()
        loop = asyncio.get_running_loop()
        with (
            patch("kenpi.main.load_config", return_value=KenpiConfig()),
            patch("kenpi.main.KenpiApiService", return_value=service),
            patch.object(
                loop,
                "add_signal_handler",
                side_effect=lambda signum, callback: handlers.update(
                    {signum: callback}
                ),
            ),
            patch.object(loop, "remove_signal_handler") as remove,
        ):
            task = asyncio.create_task(_serve(Path("/unused/test-config.toml")))
            await asyncio.sleep(0)
            handlers[signal.SIGTERM]()
            await task
        service.start.assert_awaited_once()
        service.stop.assert_awaited_once()
        self.assertEqual(remove.call_count, 2)

    async def test_start_failure_rolls_back_and_removes_handlers(self) -> None:
        service = AsyncMock()
        service.start.side_effect = OSError("bind failed")
        loop = asyncio.get_running_loop()
        with (
            patch("kenpi.main.load_config", return_value=KenpiConfig()),
            patch("kenpi.main.KenpiApiService", return_value=service),
            patch.object(loop, "add_signal_handler"),
            patch.object(loop, "remove_signal_handler") as remove,
        ):
            with self.assertRaisesRegex(OSError, "bind failed"):
                await _serve(Path("/unused/test-config.toml"))
        service.stop.assert_awaited_once()
        self.assertEqual(remove.call_count, 2)


class SafeModeWiringTests(unittest.IsolatedAsyncioTestCase):
    async def test_safe_mode_skips_vehicle_link_and_media(self) -> None:
        config = KenpiConfig(
            vehicle_uart_enabled=True,
            vehicle_uart_device="/dev/ttyAMA0",
            vehicle_uart_baud=115200,
            media_directory="/tmp/kenpi-safe-mode-media",
        )
        service = AsyncMock()
        service.start.side_effect = OSError("stop here")
        loop = asyncio.get_running_loop()
        with (
            patch("kenpi.main.load_config", return_value=config),
            patch("kenpi.main.KenpiApiService", return_value=service) as api,
            patch("kenpi.main.VehicleLinkService") as link,
            patch("kenpi.main.LocalMediaLibrary") as library,
            patch.object(loop, "add_signal_handler"),
            patch.object(loop, "remove_signal_handler"),
        ):
            with self.assertRaises(OSError):
                await _serve(Path("/unused.toml"), safe_mode=True)
        link.assert_not_called()
        library.assert_not_called()
        self.assertTrue(api.call_args.kwargs["safe_mode"])

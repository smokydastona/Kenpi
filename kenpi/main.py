"""Command-line entry point for the read-only local status service (GET routes only)."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from pathlib import Path

from kenpi.api.app import KenpiApiService, capability_payload, disabled_state
from kenpi.core.capabilities import INITIAL_CAPABILITIES
from kenpi.core.config import load_config
from kenpi.core.events import EventBus, EventType
from kenpi.features import FeatureStore
from kenpi.notify.notifier import Notifier
from kenpi.core.safemode import DEFAULT_MARKER, safe_mode_requested
from kenpi.media.library import LocalMediaLibrary
from kenpi.services.coordinator import ServiceCoordinator
from kenpi.services.health import collect_host_health
from kenpi.vehicle.link import SerialPort, VehicleCoordinator, VehicleLinkService

logger = logging.getLogger(__name__)


def _build_gateway(config, events, library, link, get_coordinator, assistant=None):
    """Authenticated gateway; imports are local so cryptography is only needed when enabled."""
    import ipaddress
    from pathlib import Path

    from kenpi.gateway.server import GatewayServer
    from kenpi.remote.wireguard import RemoteState
    from kenpi.security.devices import DeviceRegistry
    from kenpi.security.tls import ensure_certificate, server_context

    state_dir = Path(config.state_directory) / "gateway"
    registry = DeviceRegistry(state_dir)
    cert, key, fingerprint = ensure_certificate(state_dir, list(config.gateway_hosts))
    logger.info("Gateway TLS fingerprint (SHA-256): %s", fingerprint)
    remote_state = RemoteState(state_dir)
    networks = (
        (ipaddress.ip_network(config.wireguard_network),)
        if config.wireguard_network
        else ()
    )

    def snapshot(scopes):
        state: dict[str, object] = {}
        if "status.read" in scopes:
            state["status"] = {
                "coordinator": get_coordinator().describe(),
                **capability_payload(INITIAL_CAPABILITIES),
            }
        if "vehicle.read" in scopes:
            state["vehicle"] = (
                {"enabled": True, "link": link.snapshot().describe()}
                if link
                else {
                    "enabled": False,
                    **disabled_state(
                        INITIAL_CAPABILITIES, "vehicle_power_coordination"
                    ),
                }
            )
        if "camera.read" in scopes:
            # No camera transport exists, so the only truthful state is "unavailable".
            state["camera"] = disabled_state(INITIAL_CAPABILITIES, "camera_control")
        if "media.read" in scopes:
            state["media"] = (
                {"local_enabled": True, "storage": library.storage()}
                if library
                else {"local_enabled": False}
            )
        return state

    def wireguard_info(device):
        if not config.wireguard_network:
            return None
        from kenpi.remote.wireguard import client_parameters

        return client_parameters(
            registry, config.wireguard_network, config.wireguard_port,
            state_dir / "wireguard" / "server.key", device.device_id,
        )  # fmt: skip

    return GatewayServer(
        registry,
        events,
        snapshot,
        config.gateway_hosts,
        config.gateway_port,
        server_context(cert, key),
        remote_networks=networks,
        remote_enabled=remote_state.enabled,
        wireguard_info=wireguard_info,
        assistant_handler=(
            lambda device, method, path, body: assistant.phone_request(
                device.device_id, device.name, method, path, body
            )
        )
        if assistant is not None
        else None,
    )


def _build_assistant(config, events, library, link, coordinator_status):
    """Optional feature; construction failure must never stop the core service."""
    from kenpi.assistant import AssistantService

    try:
        return AssistantService(
            Path(config.state_directory) / "assistant",
            events,
            collect_host_health,
            INITIAL_CAPABILITIES,
            coordinator=coordinator_status,
            vehicle=(lambda: link.snapshot().describe()) if link else None,
            diagnose=link.diagnose if link else None,
            media_library=library,
        )
    except Exception:
        logger.exception("Assistant failed to initialise; continuing without it")
        return None


async def _serve(config_path: Path, safe_mode: bool = False) -> None:
    config = load_config(config_path)
    events = EventBus()
    if safe_mode:
        logger.warning("Safe mode: vehicle link and media library are not started")
        events.publish(EventType.SAFE_MODE_ENTERED, "main")
    # The API service needs a reference to the coordinator for its status endpoint, but the
    # coordinator needs the already-constructed service. Resolve the cycle with a one-element
    # list that is filled immediately after construction, before the service can ever be
    # started (and therefore before any request can reach status_provider).
    coordinator_ref: list[ServiceCoordinator] = []
    link: VehicleLinkService | None = None
    if config.vehicle_uart_enabled and not safe_mode:
        device, baud = config.vehicle_uart_device, config.vehicle_uart_baud
        link = VehicleLinkService(
            VehicleCoordinator(config.vehicle_heartbeat_timeout_seconds),
            lambda: SerialPort(device, baud),
            events=events,
        )
    library: LocalMediaLibrary | None = None
    if config.media_directory and not safe_mode:
        library = LocalMediaLibrary(config.media_directory)
        removed = library.recover_interrupted_transfers()
        if removed:
            logger.warning("Removed %d interrupted partial downloads", len(removed))
    features = FeatureStore(Path(config.state_directory))
    assistant = None
    if features.is_enabled("assistant") and not safe_mode:
        assistant = _build_assistant(
            config, events, library, link, lambda: coordinator_ref[0].describe()
        )
    service = KenpiApiService(
        config.api_host,
        config.api_port,
        head_unit=config.head_unit,
        safe_mode=safe_mode,
        events=events,
        status_provider=lambda: coordinator_ref[0].describe(),
        media_library=library,
        vehicle_provider=(lambda: link.snapshot().describe()) if link else None,
        features_provider=features.describe,
        assistant=assistant,
    )
    services: list = [link] if link else []
    services.append(service)
    if config.gateway_enabled and not safe_mode:
        gateway = _build_gateway(
            config, events, library, link, lambda: coordinator_ref[0], assistant
        )
        if assistant is not None:
            assistant.companion.set_presence(gateway.connected_devices)
        services.append(gateway)
    # The link starts first so the API never reports a link that is not running.
    coordinator = ServiceCoordinator(tuple(services), events=events)
    notifier = Notifier(events, config.notify_ntfy_url, config.notify_webhook_url)
    coordinator_ref.append(coordinator)
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    installed_signals: list[signal.Signals] = []
    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(signum, stopped.set)
            installed_signals.append(signum)
        await coordinator.start()
        notifier.start()
        if assistant is not None:
            await assistant.start()
            logger.info("Optional assistant enabled")
        logger.info("Local status service started")
        await stopped.wait()
    finally:
        try:
            notifier.stop()
            if assistant is not None:
                await assistant.stop()
            await coordinator.stop()
            events.close()
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
    parser.add_argument(
        "--safe",
        action="store_true",
        help="start only the status API (skips vehicle link and media library)",
    )
    parser.add_argument(
        "--safe-mode-file",
        type=Path,
        default=DEFAULT_MARKER,
        help="marker file that requests safe mode for exactly one start",
    )
    arguments = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(
        _serve(
            arguments.config,
            safe_mode_requested(arguments.safe, arguments.safe_mode_file),
        )
    )


if __name__ == "__main__":
    main()

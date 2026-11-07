"""Safety-enforcing camera command orchestration."""

from __future__ import annotations

import asyncio

from kenpi.camera.adapter import CameraLinkAdapter, CameraProtocolCodec
from kenpi.camera.commands import CommandRegistry
from kenpi.core.errors import CommandRejected
from kenpi.core.models import CommandSafety, OperationResult


class CameraControlService:
    """Orchestrates explicitly classified operations; it is not a camera driver."""

    def __init__(
        self,
        adapter: CameraLinkAdapter,
        codec: CameraProtocolCodec,
        commands: CommandRegistry,
        timeout_seconds: float = 3.0,
    ) -> None:
        if not 0.1 <= timeout_seconds <= 60:
            raise ValueError("timeout must be between 0.1 and 60 seconds")
        self._adapter = adapter
        self._codec = codec
        self._commands = commands
        self._timeout = timeout_seconds

    async def execute(
        self,
        command: str,
        payload: bytes = b"",
        *,
        user_initiated: bool = False,
    ) -> OperationResult[bytes]:
        safety = self._commands.classify(command)
        if safety in {CommandSafety.UNKNOWN, CommandSafety.UNSUPPORTED}:
            raise CommandRejected(f"command is not supported: {command}")
        if safety is not CommandSafety.READ_ONLY and not user_initiated:
            raise CommandRejected("writes require explicit user initiation")

        try:
            encoded = self._codec.encode_request(command, payload)
        except Exception as exc:
            return OperationResult(
                request_created=False,
                error=f"camera request could not be encoded: {type(exc).__name__}",
            )
        try:
            response = await asyncio.wait_for(
                self._adapter.request(encoded, self._timeout),
                timeout=self._timeout,
            )
        except (TimeoutError, asyncio.TimeoutError):
            return OperationResult(
                request_created=True, error="camera request timed out"
            )
        except Exception as exc:
            return OperationResult(
                request_created=True,
                error=f"camera transport failed: {type(exc).__name__}",
            )

        try:
            decoded = self._codec.decode_response(response.payload)
        except Exception as exc:
            return OperationResult(
                request_created=True,
                transmitted=True,
                response_received=True,
                error=f"camera response could not be decoded: {type(exc).__name__}",
            )
        return OperationResult(
            request_created=True,
            transmitted=True,
            response_received=True,
            value=decoded,
        )

"""Safety-enforcing camera command orchestration."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from kenpi.camera.adapter import (
    CameraLinkAdapter,
    CameraProtocolCodec,
    TransportResponse,
)
from kenpi.camera.commands import CommandRegistry
from kenpi.camera.session import CameraSessionProvider, SessionSnapshot, SessionState
from kenpi.core.errors import CommandRejected
from kenpi.core.models import CommandSafety, EvidenceLevel, OperationResult


class CameraControlService:
    """Serialize validated operations; it is not a camera driver.

    Production requires E4 or stronger command evidence. ``test_mode`` bypasses
    only that evidence gate for test doubles, never classification or validation.
    Optional session snapshots are bounded and do not trigger authentication.
    """

    def __init__(
        self,
        adapter: CameraLinkAdapter,
        codec: CameraProtocolCodec,
        commands: CommandRegistry,
        timeout_seconds: float = 3.0,
        *,
        minimum_evidence: EvidenceLevel = EvidenceLevel.E4,
        test_mode: bool = False,
        session_provider: CameraSessionProvider | None = None,
    ) -> None:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not 0.1 <= timeout_seconds <= 60
        ):
            raise ValueError("timeout must be between 0.1 and 60 seconds")
        if not isinstance(test_mode, bool):
            raise ValueError("test_mode must be a bool")
        if (
            not isinstance(minimum_evidence, EvidenceLevel)
            or minimum_evidence < EvidenceLevel.E4
        ):
            raise ValueError("production evidence threshold must be E4 or higher")
        self._adapter = adapter
        self._codec = codec
        self._commands = commands
        self._timeout = timeout_seconds
        self._minimum_evidence = minimum_evidence
        self._test_mode = test_mode
        self._session_provider = session_provider
        self._request_lock = asyncio.Lock()

    async def execute(
        self,
        command: str,
        payload: bytes = b"",
        *,
        user_initiated: bool = False,
    ) -> OperationResult[bytes]:
        definition = self._commands.get(command)
        if definition is None or definition.safety in {
            CommandSafety.UNKNOWN,
            CommandSafety.UNSUPPORTED,
        }:
            raise CommandRejected("command is not supported")
        if not self._test_mode and definition.evidence < self._minimum_evidence:
            raise CommandRejected("command lacks sufficient production evidence")
        if not isinstance(user_initiated, bool):
            raise CommandRejected("user initiation must be a bool")
        if definition.safety is not CommandSafety.READ_ONLY and not user_initiated:
            raise CommandRejected("writes require explicit user initiation")
        if not isinstance(payload, bytes) or definition.payload_validator is None:
            raise CommandRejected("command requires a bytes payload and validator")
        try:
            valid = definition.payload_validator(payload)
        except Exception as exc:
            raise CommandRejected("payload validation failed") from exc
        if valid is not True:
            raise CommandRejected("payload validation did not explicitly approve")

        async with self._request_lock:
            return await self._execute_serialized(command, payload)

    @staticmethod
    def _check_session(snapshot: SessionSnapshot) -> None:
        if (
            not isinstance(snapshot, SessionSnapshot)
            or snapshot.state is not SessionState.ACTIVE
        ):
            raise CommandRejected("camera session is not active")
        expires_at = snapshot.expires_at
        if expires_at is not None:
            try:
                valid = (
                    isinstance(expires_at, datetime)
                    and expires_at.utcoffset() is not None
                    and expires_at > datetime.now(timezone.utc)
                )
            except Exception as exc:
                raise CommandRejected("camera session expiry is invalid") from exc
            if not valid:
                raise CommandRejected("camera session is expired or expiry is invalid")

    async def _execute_serialized(
        self, command: str, payload: bytes
    ) -> OperationResult[bytes]:
        snapshot = None
        if self._session_provider is not None:
            try:
                snapshot = await asyncio.wait_for(
                    self._session_provider.snapshot(), timeout=self._timeout
                )
            except Exception as exc:
                raise CommandRejected("camera session snapshot failed") from exc
            self._check_session(snapshot)

        try:
            encoded = self._codec.encode_request(command, payload)
            if not isinstance(encoded, bytes):
                raise TypeError("encoded camera request must be bytes")
        except Exception as exc:
            return OperationResult(
                request_created=False,
                error=f"camera request could not be encoded: {type(exc).__name__}",
            )
        if snapshot is not None:
            self._check_session(snapshot)
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
            if not isinstance(response, TransportResponse) or not isinstance(
                response.payload, bytes
            ):
                raise TypeError("camera transport response must contain bytes")
            decoded = self._codec.decode_response(response.payload)
            if not isinstance(decoded, bytes):
                raise TypeError("decoded camera response must be bytes")
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

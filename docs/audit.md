# Repository audit and implementation plan

Baseline: `main` at version 0.1.0. Python 3.11+, standard library only at runtime
(`ruff` is the sole dev dependency). 93 host-side unit tests pass; one IPv6 test
is skipped where `::1` cannot be bound.

## What actually exists

| Area | State |
| --- | --- |
| `kenpi/main.py`, `services/coordinator.py` | Working. Starts the loopback API, plus the vehicle link when configured; coordinator rolls back on partial startup and publishes lifecycle events. |
| `kenpi/api/app.py`, `ui/` | Working, GET-only: `/`, static assets, `/healthz` and `/api/v1/{capabilities,status,camera,preview,vehicle,media,diagnostics,head-units,events}`. Media routes serve the local library only. No camera command, write, WebSocket or authentication routes exist. |
| `core/config.py` | Strict TOML loader. Rejects `camera_enabled` and `vehicle_uart_enabled` as unimplemented and any non-loopback bind. |
| `core/capabilities.py` | Every hardware capability is `BLOCKED`/`NOT_VERIFIED` at E0. `AVAILABLE` requires E4 or higher. |
| `camera/lifecycle.py` | State machine that records caller-reported phases only; no I/O. |
| `camera/control.py`, `commands.py` | Safety gate (evidence, payload validator, serialization, user initiation). Not reachable from `main` or the API. |
| `vehicle/protocol.py` | UART framing and CRC only. No serial transport. |
| `hardware/platform.py`, `services/health.py` | Host health collection. |
| `camera/adapter.py`, `session.py`, `kenwood/adapter.py`, `media/service.py`, `video/pipeline.py` | Protocol interfaces only. No implementations. |
| `android/` | Optional diagnostic Compose client (`GatewayClient`, `GatewayStore`) with unit tests. It is not an Android Auto app and not the Kenwood display path. |
| `deploy/` | systemd unit and install/rollback scripts for the status service only. |

## Architecture boundaries

- The API is unauthenticated, so it is loopback-only. Remote Android access needs
  a separate private TLS gateway with authentication (see `docs/android-companion.md`).
- Camera, media, video, Kenwood and vehicle code are contracts or isolated
  libraries. Nothing composes them, so none of those features are usable.
- The Android companion must not become the Kenwood display solution until a
  verified display and touch path exists (`docs/kenwood-integration.md`).

## Gaps and risks

1. **No A810 Lite evidence.** Command, stream and file-access protocols are
   undiscovered; no `CameraLinkAdapter` or `CameraProtocolCodec` exists.
2. **No live preview or media implementation.** `LiveVideoPipeline`,
   `CameraMediaProvider` and `PlaybackService` have no implementers.
3. **Kenwood DMX5710S path unverified.** Display input, video format and touch
   return are undetermined. Manufacturer manuals could not be retrieved.
4. **ESP32/vehicle path unverified.** Only framing and CRC exist: no serial
   transport, firmware, or power-management logic.
5. **Unwired components.** `CameraControlService` and the lifecycle state
   machine are not constructed in `main`, and nothing updates capabilities from
   evidence at runtime (the list is static).
6. **Test and CI gaps.** No hardware-in-the-loop tests, and CI does not build or
   test the Android project. No dependency lock beyond the pinned `ruff` and `setuptools`.
7. **Environment fragility (fixed here).** The IPv6 API test checked only
   `socket.has_ipv6` and errored on hosts that cannot bind `::1`; it now probes
   the bind and skips.

## Implementation plan

Order follows the evidence gates in `docs/evidence-and-validation.md`; no step
may mark a capability `AVAILABLE` without E4 evidence.

1. **Evidence capture (E1–E3).** Record A810 Lite traffic and firmware details
   per `docs/protocol-discovery.md`; verify the DMX5710S inputs against the
   physical unit and manual.
2. **Camera transport.** Implement a `CameraLinkAdapter` and versioned codec
   from captured evidence, with replay-based tests. Keep `camera_enabled`
   rejected until this lands.
3. **Composition root.** Build a service wiring camera session, lifecycle and
   `CameraControlService`, with evidence-driven capability updates replacing
   `INITIAL_CAPABILITIES`.
4. **Preview and media.** Implement `LiveVideoPipeline` and
   `CameraMediaProvider`, keeping originals immutable, then add read-only
   routes.
5. **Kenwood display and touch.** Implement `DisplayAdapter` and `TouchAdapter`
   only after step 1 confirms a path. Gate 3 requires the physical unit.
6. **Vehicle/ESP32.** Add a UART transport on top of `vehicle/protocol.py`,
   firmware, and power coordination; validate on hardware.
7. **Remote access.** If Android access is needed, add a separate authenticated
   TLS gateway rather than loosening the loopback restriction.
8. **CI.** Add an Android Gradle test job and a hardware-validation checklist.

## Update: software-completable work added

Implemented and unit-tested (not physically verified): typed vehicle messages
with shared Python/firmware fixtures, host vehicle link with watchdog and
serial transport, local media library (atomic, non-overwriting downloads;
partial recovery), frame buffer and FPS/latency tracker, API routes for
camera/preview/vehicle/media/diagnostics with rate limiting, and dashboard
vehicle/media screens fed by those routes.

Still blocked by missing evidence or hardware: A810 Lite transport, protocol,
authentication, settings, recording control, stream decoding, camera media
listing, playback, DMX5710S display/touch path, ESP32 GPIO/UART firmware
integration, and all three physical acceptance gates. The dashboard has not
been exercised in a real browser in this environment.

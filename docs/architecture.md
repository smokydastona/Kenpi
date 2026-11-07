# Architecture status

## Current implementation

The Python package uses replaceable interfaces and separates core policy from
device integrations:

- `kenpi.core`: strict TOML configuration, evidence/capability models, errors,
  and operation results that distinguish transmission, response, acknowledgement,
  and independent verification.
- `kenpi.camera`: transport and codec protocols, explicit command classification,
  session-provider contract, and an orchestration service that rejects unknown
  commands and requires user initiation for writes.
- `kenpi.video`, `kenpi.media`, `kenpi.kenwood`: transport-independent interfaces
  for live frames, camera files/playback, display, and touch.
- `kenpi.vehicle`: framed UART messages with length bounds, CRC-16, incremental
  parsing, and modular sequence/replay checks.
- `kenpi.services`: startup and reverse-order shutdown for managed services.

These are real interfaces and safety checks, not implementations of the
camera's undocumented protocol, a video decoder, physical UART, UI, or Kenwood
display transport. Camera and hardware features remain disabled/not verified.

## Intended data flow

UI/API clients will call independent camera-control, live-preview, media, health,
and vehicle services. The camera adapter owns its verified transport; a versioned
codec and session provider own protocol/authentication. The preview pipeline is
separate from downloaded-media playback. The vehicle controller communicates
over UART and coordinates Pi availability only. No path in the current software
can switch camera power.

## Runtime and dependencies

The current host-only implementation uses Python's standard library. Python
3.11+ is required. The CI workflow compiles the package and runs `unittest` on
Python 3.11 and 3.12. A deployable server, UI, service units, or ESP32 firmware
have not yet been implemented.

## Fault isolation

Services have explicit asynchronous start/stop contracts. The coordinator stops
already-started services in reverse order when startup fails. Transport requests
are bounded by explicit timeouts. Device-specific services must report failure
without converting a generic response into an acknowledged or verified state.
The Pi, app, Kenwood, and ESP32 are not permitted to become prerequisites for
normal dashcam recording.

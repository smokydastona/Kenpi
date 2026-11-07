# Architecture status

## Current implementation

The Python package uses replaceable interfaces and separates core policy from
device integrations:

- `kenpi.core`: strict TOML configuration, evidence/capability models, errors,
  and operation results that distinguish transmission, response, acknowledgement,
  and independent verification.
- `kenpi.camera`: transport and codec protocols, explicit command classification,
  session-provider contract, an orchestration service that rejects unknown
  commands, requires E4 evidence and payload validation, serializes requests,
  checks optional session snapshots, requires user initiation for writes, and a transport-independent
  lifecycle state machine with explicit recovery and shutdown transitions.
- `kenpi.video`, `kenpi.media`, `kenpi.kenwood`: transport-independent interfaces
  for live frames, camera files/playback, display, and touch.
- `kenpi.vehicle`: framed UART messages with length bounds, CRC-16, incremental
  parsing, and modular sequence/replay checks.
- `kenpi.services`: serialized startup and reverse-order shutdown for managed
  services, including partial startup, cancellation-safe cleanup, and preserved
  startup/rollback failures. The CLI uses this coordinator.
- `kenpi.api` and `kenpi.ui`: a loopback-only read-only HTTP API and a responsive
  capability and host-health dashboard that displays service-reported states
  without mock data and clears values after failed refreshes. IPv4 and IPv6
  loopback are supported; request concurrency and socket waits are bounded.
- `kenpi.services.health`: best-effort Linux memory, storage, and thermal
  readings; unavailable host readings are returned as null, not fabricated.
- `kenpi.hardware`: concrete read-only device-tree/kernel platform observation
  for Pi 4B and Pi 5, integrated with health collection. It does not authorize
  GPIO operations or assign unverified board pins.
- `android`: optional read-only diagnostic client, not a Kenwood display adapter.
  Polling stops when its activity stops, and obsolete snapshots are discarded.

The camera lifecycle state machine only records caller-reported phases; it does
not discover, connect to, authenticate with, retry, or shut down a camera
transport. These are real interfaces and safety checks, not implementations of
the camera's undocumented protocol, a video decoder, physical UART, full camera
control UI, or Kenwood display transport. Camera and hardware features remain
disabled/not verified.

## Intended data flow

UI/API clients will call independent camera-control, live-preview, media, health,
and vehicle services. The camera adapter owns its verified transport; a versioned
codec and session provider own protocol/authentication. The preview pipeline is
separate from downloaded-media playback. The vehicle controller communicates
over UART and coordinates Pi availability only. No path in the current software
can switch camera power.

## Runtime and dependencies

The host-only implementation uses Python's standard library at runtime. Python
3.11+ is required. The development extra pins Ruff for formatting/linting; CI
also compiles the package and runs `unittest` on Python 3.11 and 3.12. The
read-only status server/UI and supervised installation resources are implemented.
Camera operations, ESP32 firmware, and physical display integration remain
externally blocked. The Android toolchain is pinned, but full Android compilation
requires reachable Google Maven artifacts; see the companion document.

## Fault isolation

Services have explicit asynchronous start/stop contracts. The coordinator stops
partially started and fully started services in reverse order when startup fails.
Stop operations finish even if their caller is cancelled; errors retain both
startup and cleanup causes. Transport requests
are bounded by explicit timeouts. Device-specific services must report failure
without converting a generic response into an acknowledged or verified state.
The Pi, app, Kenwood, and ESP32 are not permitted to become prerequisites for
normal dashcam recording.

## Baseline comparison

On 2026-11-07, fetched `main` and the initial Copilot checkout both resolved to
`a0e7d9977077a4554c60523d6c1e4c61cbd11e16`, also their merge base. Both-direction
logs and file diffs were empty; there were no divergent files to merge or reject.
The unchanged baseline passed 33 host tests. This replaces the earlier assumption
of divergence with an exact comparison; no merge was performed.

The existing source contracts are retained but are not counted as implemented
camera, preview, media, or hardware-control features. They cannot acquire a real
driver solely from software tests. Independent platform observation, safety,
status, lifecycle, and deployment work is implemented without enabling those
blocked paths.

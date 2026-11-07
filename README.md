# Kenpi

**Turning the Kenwood touchscreen into the command centre for the 70mai A810 Lite.**

Kenpi is a Raspberry Pi-based control gateway intended to make a Kenwood
DMX5710S touchscreen the primary user interface for a 70mai A810 Lite dashcam.
It is an auxiliary control and presentation system—not a replacement for the
camera's own recording, storage, or safety-critical operation.

## Mission

The intended Kenwood-facing capabilities are:

- Live camera preview
- Camera settings and recording control
- Camera file browsing and playback
- Camera and gateway status monitoring

These are project goals, not claims of demonstrated compatibility. Each
capability remains unverified until supported by evidence from the actual A810
Lite, Raspberry Pi, and Kenwood hardware.

## Non-negotiable design requirements

- **Independent recording:** The A810 Lite must continue recording if Kenpi
  crashes, loses power, reboots, or disconnects.
- **No invented protocol:** No endpoint, port, command, authentication flow, or
  stream format is considered supported until observed and reproduced on the
  target A810 Lite.
- **Separate validation gates:** Camera control, live preview, and Kenwood
  interaction are validated independently.
- **Fail-safe behavior:** Kenpi failure must not send repeated or unsafe camera
  commands or interfere with autonomous recording.
- **Evidence-backed status:** Documentation, mocks, open ports, and successful
  compilation do not constitute hardware acceptance.
- **Local-first operation:** Core camera interaction is intended to use the
  local camera connection without depending on a cloud service unless testing
  establishes that a specific operation requires otherwise.

## Current status

The repository contains a Python service foundation, a loopback-only read-only
status API and dashboard, safety policies, and host-independent tests. The
dashboard reports declared capabilities and available host health data; it
does not control the camera. Camera protocol behavior, preview, media access,
physical Kenwood integration, and vehicle hardware remain unverified.

Host-side hardening includes IPv6 loopback, bounded HTTP handlers, cancellation-safe
service coordination, evidence-gated serialized camera command policy, and
freshness-aware status displays. Read-only Pi platform observation and supervised
installation resources are documented in [Deployment](docs/deployment.md).
These are not camera drivers or hardware acceptance.

### Validation gates

| Gate | Required proof | Status |
|---|---|---|
| **Gate 1 — Camera control** | Pi changes a real setting; independent read-back and camera-side verification confirm it | **NOT VERIFIED** |
| **Gate 2 — Live preview** | Pi receives and decodes a live A810 Lite frame; evidence records format, resolution, frame rate, and latency | **NOT VERIFIED** |
| **Gate 3 — Kenwood control** | Physical DMX5710S touchscreen interaction controls the running Kenpi interface | **NOT VERIFIED** |

See [Evidence and validation](docs/evidence-and-validation.md) for evidence
levels and physical acceptance criteria.

## Hardware baseline

The documented baseline is treated as locked unless testing identifies a
concrete technical blocker:

- **Primary gateway:** Raspberry Pi 5, 8 GB
- **Development/compatibility alternative:** Raspberry Pi 4B, 4 GB or 8 GB
- **Auxiliary controller:** SX1262 LoRa V3 ESP32-S3 board; exact physical board
  revision must be verified

See [Hardware standard](docs/hardware-standard.md). Hardware is not available
for validation in this repository environment; no hardware readiness is
claimed.

## Architecture and first milestone

The architecture separates camera transport and protocol/session handling,
camera control, live preview, media, UI, Kenwood integration, vehicle
coordination, and diagnostics. See [Architecture](docs/architecture.md) for
boundaries, data flows, failure isolation, and implementation status.

The highest-risk unknown is whether the A810 Lite exposes reproducible local
control and a usable live-preview transport to an independent client. The
[Protocol discovery](docs/protocol-discovery.md) document defines the cautious,
evidence-led investigation. Behavior reported for other 70mai models is a
research lead only—not proof of A810 Lite compatibility.

## Development

Requires Python 3.11 or newer. Install and run the host-only checks:

```sh
python -m pip install -e '.[dev]'
ruff format --check kenpi tests
ruff check kenpi tests
python -m compileall -q kenpi tests
python -m unittest discover -v
```

Run the local status dashboard with:

```sh
kenpi --config config/kenpi.example.toml
```

The read-only native Android companion project is in [`android/`](android/).
It is an optional diagnostic client, not an assumed Kenwood display solution.
See [Android companion](docs/android-companion.md) for build instructions and
the API's current remote-access limitations.

The independent auxiliary controller firmware (protocol port + vehicle power
state machine, no hardware I/O) is in [`firmware/`](firmware/). It mirrors
`kenpi/vehicle/protocol.py` in C++ and ships with no default GPIO assignments;
see [`firmware/README.md`](firmware/README.md) for what is and is not
implemented and why.

The API binds to loopback and exposes health, capabilities, status, and static
dashboard content only. Camera and vehicle transports are not implemented or
enabled. Host-only tests use explicitly labeled test doubles and do not count
as hardware acceptance.

CI (`.github/workflows/ci.yml`) runs the Python checks above on 3.11/3.12 and
builds a wheel, ShellChecks `deploy/` and `os-image/` scripts, verifies
`deploy/kenpi-status.service` with `systemd-analyze verify`, runs the firmware
project's host-executable native unit tests, and runs the Android project's
JVM unit tests (`./gradlew testDebugUnitTest`). None of these CI jobs build or
run anything against real camera, Kenwood, or ESP32 hardware.

## Security and privacy

Packet captures and logs can contain Wi-Fi credentials, session tokens, device
identifiers, GPS coordinates, or personal media metadata. Keep original
captures private where necessary, redact secrets from published reports, and
never commit credentials, private keys, private media, or unreviewed traffic
captures. See [Security](docs/security.md).

## Project principle

**The camera records independently. Kenpi adds control and visibility.**

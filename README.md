# Kenpi

**Turning the Kenwood touchscreen into the command centre for the 70mai A810 Lite.**

Kenpi is a Raspberry Pi gateway project. This repository currently contains a
Python service foundation, safety policies, and host-independent tests; it does
not yet control or display an A810 Lite.

## Safety and verification status

- Independent camera recording remains the design invariant: Kenpi and the
  auxiliary controller have no camera power-control interface.
- A810 Lite transport, authentication, commands, preview, and media access are
  intentionally unimplemented until supported by camera-specific evidence.
- The Kenwood display and touch path has not been verified on physical hardware.
- Camera control, live preview, and touchscreen validation gates are all
  **NOT VERIFIED**. See [evidence and validation](docs/evidence-and-validation.md).

## Development

Requires Python 3.11 or newer. Run host-only checks with:

```sh
python -m compileall -q kenpi tests
python -m unittest discover -v
```

The TOML example in `config/kenpi.example.toml` binds locally and leaves
hardware services disabled. Production configuration rejects a non-loopback API
bind until API authentication is implemented.

## Architecture and blockers

See [architecture](docs/architecture.md), [protocol discovery](docs/protocol-discovery.md),
[hardware standard](docs/hardware-standard.md), and
[vehicle power safety](docs/vehicle-power-management.md). No camera-specific
protocol claims or physical test results are included.

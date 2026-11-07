# Kenpi auxiliary controller firmware

This directory contains the source for an independent microcontroller
("auxiliary controller", e.g. an ESP32-S3 class board) that speaks the
framed UART protocol already defined on the Raspberry Pi side in
`kenpi/vehicle/protocol.py`. It exists to close part of the gap described in
`docs/vehicle-power-management.md`: "This host-side framing code is not yet
paired with ESP32 firmware."

## What is implemented here (verified, hardware-independent)

- `include/kenpi/protocol.h` / `src/protocol.cpp` — a byte-for-byte C++ port
  of `kenpi/vehicle/protocol.py`: the same magic bytes, header layout,
  CRC-16/CCITT-FALSE algorithm, incremental/resynchronizing decoder, and
  sequence-replay rejection. This is what prevents protocol drift between
  the two sides: both implementations are tested against equivalent
  scenarios (see `tests/test_vehicle_protocol.py` on the Python side and
  `test/test_protocol.cpp` here).
- `include/kenpi/power_state_machine.h` / `src/power_state_machine.cpp` — a
  pure, I/O-free vehicle power coordination state machine implementing the
  states described in the project's design notes (`VEHICLE_OFF`,
  `IGNITION_DETECTED`, `STARTING`, `RUNNING`, `IGNITION_LOST`,
  `SHUTDOWN_PENDING`, `SHUTTING_DOWN`, `SLEEPING`, `WAKING`, `FAULT`). It is
  driven entirely by caller-supplied signals and a caller-supplied
  monotonic clock — it never reads GPIO itself and it never controls
  dashcam power, consistent with the safety invariant in
  `docs/vehicle-power-management.md`.
- `include/kenpi/board_config.h` / `src/board_config.cpp` — a pin
  configuration structure and validator that ships with **no default or
  guessed GPIO assignments**. It refuses to validate until every required
  pin is explicitly set and non-colliding. This is intentional: assigning
  real pin numbers requires bench-verifying the exact physical board
  revision first (see "What is explicitly NOT implemented" below).
- `test/test_protocol.cpp`, `test/test_power_state_machine.cpp` — plain,
  dependency-free C++ unit tests (no Unity/GoogleTest/Arduino needed).
  Run them with:

  ```sh
  firmware/tools/run_native_tests.sh
  ```

  This compiles and executes the tests with the host's own `g++` (C++17),
  with no network access and no PlatformIO installation required. These
  tests are also run in CI (see the `firmware` job in
  `.github/workflows/ci.yml`).

## What is explicitly NOT implemented (and why)

- **Real GPIO/UART wiring in `src/main.cpp`.** The Arduino `setup()`/`loop()`
  sketch wires the protocol decoder and power state machine together, but
  it refuses to touch any pin because `BoardConfig` ships unconfigured.
  Assigning actual GPIO numbers for ignition sensing, UART, or a Pi wake
  line requires physically verifying the exact board revision, UART
  voltage levels, isolation/protection, and ignition sense circuit on a
  bench, per `docs/vehicle-power-management.md`. Guessing pin numbers here
  would be fabricating hardware behavior this project explicitly refuses to
  do.
- **Building/flashing the `esp32s3` PlatformIO environment.** This requires
  PlatformIO to download Espressif's toolchain and framework packages from
  its package registry, which this development environment cannot reach.
  Only the `native` logic (protocol + state machine + board config
  validation) has been compiled and tested here; the real device build is
  unverified and must be built/flashed on a machine with normal internet
  access and then validated on a bench before any vehicle installation.
- **Acknowledgement/heartbeat message schemas, watchdog recovery behavior,
  and the exact Pi-side integration that consumes this firmware's output.**
  These depend on protocol/application-layer decisions that haven't been
  made yet and aren't fabricated here.

## Verifying before vehicle installation

Per `docs/vehicle-power-management.md`, do not install or test this
firmware in a vehicle, and do not test on a public road, until the board
revision, pinout, UART levels, isolation, ignition sense circuit, brownout
behavior, watchdog recovery, Pi shutdown sequence, and communication-loss
behavior have all been verified on a bench.

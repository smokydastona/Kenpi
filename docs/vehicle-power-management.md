# Vehicle power coordination

## Safety invariant

Ignition sensing and Pi shutdown coordination must never control dashcam power.
The A810 Lite remains independently powered and recording according to its own
vehicle/power configuration when the Pi, software, UART, or ESP32 is unavailable.
The current application has no camera power-control API.

## Host protocol foundation

`kenpi.vehicle.protocol` defines a bounded UART frame: magic `KP`, protocol
version, message type, 16-bit sequence, 16-bit payload length, payload (maximum
512 bytes), and CRC-16/CCITT-FALSE (initial value `0xffff`, polynomial
`0x1021`, transmitted big-endian). The incremental parser supports fragmented
reads, rejects bad checksums/oversized frames, and skips unsupported versions.
`SequenceTracker` rejects duplicate/stale sequence values and accepts natural
16-bit wraparound.

`firmware/` now contains a hardware-independent C++ port of this protocol plus a
pure vehicle power coordination state machine (`VEHICLE_OFF`,
`IGNITION_DETECTED`, `STARTING`, `RUNNING`, `IGNITION_LOST`,
`SHUTDOWN_PENDING`, `SHUTTING_DOWN`, `SLEEPING`, `WAKING`, `FAULT`), both
covered by host-executable unit tests (`firmware/tools/run_native_tests.sh`).
Neither component reads GPIO or controls dashcam power. What remains
unimplemented: acknowledgments, ignition/sensor message schemas, watchdog
behavior, electrical validation, and any real pin assignment (the firmware's
`BoardConfig` ships unconfigured and refuses to validate until bench-verified
pins are supplied — see `firmware/README.md`). The parser does not authorize
hardware actions. Loss-of-link behavior must leave camera recording
unaffected.

## Typed messages and host link

`kenpi.vehicle.messages` (mirrored in `firmware/include/kenpi/messages.h`)
defines `HEARTBEAT` (0x01), `POWER_STATE` (0x02), `SHUTDOWN_REQUEST` (0x03) and
`SHUTDOWN_ACK` (0x04). `protocol/vehicle_frames.txt` holds shared byte-exact
fixtures verified by both the Python tests and
`firmware/tools/run_native_tests.sh`.

`kenpi.vehicle.link` provides `VehicleCoordinator` (sequence/replay rejection,
heartbeat watchdog, snapshot), `VehicleLinkService` and a termios `SerialPort`
(tested against a pseudo-terminal, not a real controller). Enable with
`vehicle_uart_enabled`, `vehicle_uart_device` and an explicit
`vehicle_uart_baud`. Link loss only changes the reported state. A
`SHUTDOWN_REQUEST` is acknowledged only when an injected handler accepts it;
`main` configures none, so requests are reported but not acted on or
acknowledged. Firmware does not yet send these messages (no GPIO/UART
integration without a bench-verified `BoardConfig`).

## Physical validation required

Verify the exact board/pinout, UART voltage levels, isolation/protection, ignition
sense circuit, brownout behavior, watchdog recovery, Pi clean-shutdown sequence,
and communication-loss behavior on a bench before vehicle installation. Do not
test on a public road.

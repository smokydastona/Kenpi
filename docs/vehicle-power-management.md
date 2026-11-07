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

## Physical validation required

Verify the exact board/pinout, UART voltage levels, isolation/protection, ignition
sense circuit, brownout behavior, watchdog recovery, Pi clean-shutdown sequence,
and communication-loss behavior on a bench before vehicle installation. Do not
test on a public road.

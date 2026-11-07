// Arduino-framework entry point wiring the protocol decoder and power
// state machine to real UART/GPIO I/O. This file only compiles under
// PlatformIO's `esp32s3` environment (it includes <Arduino.h>); it has
// never been built or flashed in this development environment (no
// Espressif toolchain/package access here — see firmware/README.md).
//
// Per docs/vehicle-power-management.md, this firmware must NEVER control
// dashcam power, and it refuses to touch any GPIO until a bench-verified
// `BoardConfig` has been supplied (see kenpi/board_config.h). Until that
// configuration is provided, the firmware only logs over the native USB
// serial console and does nothing else — it does not guess pins, does not
// assert any output, and does not claim to control ignition sensing or Pi
// power.
#include <Arduino.h>

#include "kenpi/board_config.h"
#include "kenpi/power_state_machine.h"
#include "kenpi/protocol.h"

namespace {

// NOTE: Left intentionally unconfigured. Fill in only after verifying the
// exact physical board revision and pinout on a bench; see
// firmware/README.md and docs/vehicle-power-management.md. The firmware
// deliberately refuses to initialize GPIO/UART while this is unconfigured.
kenpi::BoardConfig g_board_config;

// NOTE: Placeholder timing values. Replace with vehicle- and
// board-specific figures derived from bench testing before relying on this
// firmware in a vehicle; see firmware/README.md.
kenpi::PowerStateMachineConfig g_power_config{
    /*ignition_debounce_ms=*/250,
    /*startup_delay_ms=*/2000,
    /*shutdown_delay_ms=*/5000,
    /*shutdown_ack_timeout_ms=*/10000,
    /*link_loss_timeout_ms=*/3000,
};

kenpi::VehiclePowerStateMachine g_power_machine;
kenpi::VehicleMessageDecoder g_decoder;
kenpi::SequenceTracker g_sequence_tracker;
bool g_board_ready = false;

}  // namespace

void setup() {
  Serial.begin(115200);
  const char* error = nullptr;

  if (!g_board_config.IsValid(&error)) {
    Serial.print("FAULT: board configuration is unverified/unsafe: ");
    Serial.println(error ? error : "unknown");
    Serial.println(
        "Refusing to initialize GPIO/UART. Supply a bench-verified "
        "BoardConfig (see firmware/README.md) before deploying.");
    g_board_ready = false;
    return;
  }

  if (!kenpi::VehiclePowerStateMachine::Create(g_power_config, millis(), &g_power_machine,
                                                &error)) {
    Serial.print("FAULT: power state machine configuration invalid: ");
    Serial.println(error ? error : "unknown");
    g_board_ready = false;
    return;
  }

  g_board_ready = true;
  Serial.println("Board configuration accepted; power state machine initialized.");
}

void loop() {
  if (!g_board_ready) {
    // Fail-safe idle loop: no GPIO is touched, and the dashcam's own power
    // management is entirely unaffected by this controller being
    // unconfigured.
    delay(1000);
    return;
  }

  // Real GPIO sampling and UART I/O are intentionally not implemented here:
  // doing so requires the bench-verified pin assignments this firmware
  // refuses to assume. Once `g_board_config` is supplied and verified,
  // integrate real `digitalRead`/`Serial1` calls here, feeding sampled
  // bytes into `g_decoder.Feed(...)` and sampled ignition state into
  // `g_power_machine.Update(...)`.
}

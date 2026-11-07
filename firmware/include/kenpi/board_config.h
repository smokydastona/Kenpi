// Board pin configuration loader for the independent auxiliary controller.
//
// This module intentionally ships with NO default or hardcoded pin
// assignments. `docs/vehicle-power-management.md` requires verifying the
// exact physical board/pinout, UART voltage levels, isolation/protection,
// and ignition sense circuit on a bench before any pin is assigned; doing
// that verification is out of scope for software and must not be
// fabricated. Instead, this module defines the *shape* of a valid
// configuration and a validator that refuses to proceed when pins are
// unset, duplicated, or reserved, so firmware can fail safe (refuse to
// initialize) until a real, bench-verified configuration is supplied by
// whoever integrates this controller with a specific board revision.
#ifndef KENPI_BOARD_CONFIG_H_
#define KENPI_BOARD_CONFIG_H_

#include <cstdint>

namespace kenpi {

// Sentinel meaning "not configured". No valid GPIO assignment may use this
// value; `BoardConfig::IsValid` rejects any pin still set to it.
inline constexpr std::int16_t kUnconfiguredPin = -1;

struct BoardConfig {
  // GPIO used to sense the vehicle ignition/accessory signal (input).
  std::int16_t ignition_sense_pin = kUnconfiguredPin;
  // UART pins used for the framed protocol link to the Raspberry Pi.
  std::int16_t uart_tx_pin = kUnconfiguredPin;
  std::int16_t uart_rx_pin = kUnconfiguredPin;
  // Optional wake line asserted to signal the Pi to power on. Leave
  // unconfigured (kUnconfiguredPin) if the integration does not use one;
  // unlike the other pins, this one is allowed to be absent.
  std::int16_t pi_wake_pin = kUnconfiguredPin;

  // The highest GPIO number considered representable on the target board.
  // Must be set by the integrator from the verified board documentation;
  // it is not inferred or guessed here.
  std::int16_t max_valid_gpio = kUnconfiguredPin;

  // Returns true only if every required pin is set, within
  // [0, max_valid_gpio], and no two required pins collide. On failure,
  // `error` (if non-null) is set to a human-readable reason. This refuses
  // unsafe or incomplete configurations rather than guessing or defaulting
  // pin numbers.
  bool IsValid(const char** error) const;
};

}  // namespace kenpi

#endif  // KENPI_BOARD_CONFIG_H_

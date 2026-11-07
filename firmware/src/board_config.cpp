#include "kenpi/board_config.h"

namespace kenpi {

namespace {

bool IsRequiredPinValid(std::int16_t pin, std::int16_t max_valid_gpio) {
  return pin != kUnconfiguredPin && pin >= 0 && pin <= max_valid_gpio;
}

}  // namespace

bool BoardConfig::IsValid(const char** error) const {
  if (max_valid_gpio == kUnconfiguredPin || max_valid_gpio < 0) {
    if (error) *error = "max_valid_gpio must be set from verified board documentation";
    return false;
  }
  if (!IsRequiredPinValid(ignition_sense_pin, max_valid_gpio)) {
    if (error) *error = "ignition_sense_pin is unconfigured or out of range";
    return false;
  }
  if (!IsRequiredPinValid(uart_tx_pin, max_valid_gpio)) {
    if (error) *error = "uart_tx_pin is unconfigured or out of range";
    return false;
  }
  if (!IsRequiredPinValid(uart_rx_pin, max_valid_gpio)) {
    if (error) *error = "uart_rx_pin is unconfigured or out of range";
    return false;
  }
  if (pi_wake_pin != kUnconfiguredPin &&
      (pi_wake_pin < 0 || pi_wake_pin > max_valid_gpio)) {
    if (error) *error = "pi_wake_pin is out of range";
    return false;
  }

  const std::int16_t required[] = {ignition_sense_pin, uart_tx_pin, uart_rx_pin};
  for (std::size_t i = 0; i < 3; ++i) {
    for (std::size_t j = i + 1; j < 3; ++j) {
      if (required[i] == required[j]) {
        if (error) *error = "two required pins are assigned to the same GPIO";
        return false;
      }
    }
    if (pi_wake_pin != kUnconfiguredPin && required[i] == pi_wake_pin) {
      if (error) *error = "pi_wake_pin collides with a required pin";
      return false;
    }
  }
  return true;
}

}  // namespace kenpi

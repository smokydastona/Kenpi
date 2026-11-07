// Pure, I/O-free vehicle power coordination state machine for the
// independent auxiliary controller (ESP32-class microcontroller).
//
// This machine never touches hardware directly: it is driven entirely by
// caller-supplied boolean signals and a caller-supplied monotonic
// millisecond clock, and it never asserts control over dashcam power. Per
// `docs/vehicle-power-management.md`, the A810 Lite remains independently
// powered and must keep recording according to its own configuration
// regardless of whether this state machine, the UART link, or the Pi is
// available. This file intentionally contains no GPIO reads/writes, no pin
// numbers, and no board-specific behavior: callers are responsible for
// sampling physical signals and translating this machine's state into any
// real output lines only after the board's exact pinout has been verified
// on a bench (see `firmware/README.md`).
#ifndef KENPI_POWER_STATE_MACHINE_H_
#define KENPI_POWER_STATE_MACHINE_H_

#include <cstdint>

namespace kenpi {

enum class PowerState {
  kVehicleOff,
  kIgnitionDetected,
  kStarting,
  kRunning,
  kIgnitionLost,
  kShutdownPending,
  kShuttingDown,
  kSleeping,
  kWaking,
  kFault,
};

const char* ToString(PowerState state);

struct PowerStateMachineConfig {
  // Minimum time (ms) a raw ignition signal must be stable before it is
  // treated as debounced.
  std::uint32_t ignition_debounce_ms = 0;
  // Grace period (ms) after debounced ignition-on before the state machine
  // advances past kIgnitionDetected (tolerates engine cranking glitches).
  std::uint32_t startup_delay_ms = 0;
  // Grace period (ms) after ignition is lost before a shutdown request is
  // raised (tolerates brief ignition blips, e.g. engine restarts).
  std::uint32_t shutdown_delay_ms = 0;
  // Maximum time (ms) kShutdownPending may wait for the link to go down
  // before the machine gives up waiting and moves on regardless.
  std::uint32_t shutdown_ack_timeout_ms = 0;
  // Maximum time (ms) without a healthy UART link before kRunning is
  // considered to have lost communication and moves to kFault. This is a
  // diagnostic signal only; it never powers anything down.
  std::uint32_t link_loss_timeout_ms = 0;

  // Returns true if every field is a nonzero, sane duration. A machine must
  // never be constructed from an unvalidated/incomplete configuration.
  bool IsValid(const char** error) const;
};

// Pure state machine: `Update` is the only entry point, and all timing
// decisions are made relative to the `now_ms` argument the caller supplies
// (never an internal clock), so the whole machine is deterministic and
// testable without real time passing.
class VehiclePowerStateMachine {
 public:
  // Fails (returns false) rather than constructing a machine from an
  // invalid configuration.
  static bool Create(const PowerStateMachineConfig& config, std::uint32_t now_ms,
                      VehiclePowerStateMachine* out, const char** error);

  // Public so callers can hold an instance (e.g. as a member variable or
  // stack value) before it is initialized via `Create`. A
  // default-constructed machine starts in kVehicleOff with an all-zero
  // configuration; callers must call `Create` before relying on any
  // timing-dependent behavior.
  VehiclePowerStateMachine() = default;

  PowerState state() const { return state_; }

  // Evaluates one tick of input and returns the (possibly unchanged) state.
  // `ignition_raw` is the unfiltered sensed ignition level; `link_ok`
  // reflects whether a recent heartbeat/message was seen on the UART link;
  // `brownout` reflects a caller-detected undervoltage/reset condition.
  PowerState Update(bool ignition_raw, bool link_ok, bool brownout, std::uint32_t now_ms);

 private:
  void EnterState(PowerState next, std::uint32_t now_ms);
  void UpdateDebounce(bool ignition_raw, std::uint32_t now_ms);
  std::uint32_t TimeInState(std::uint32_t now_ms) const;

  PowerStateMachineConfig config_{};
  PowerState state_ = PowerState::kVehicleOff;
  std::uint32_t state_entered_at_ms_ = 0;

  bool debounced_ignition_ = false;
  bool raw_ignition_candidate_ = false;
  std::uint32_t raw_change_at_ms_ = 0;

  std::uint32_t last_link_ok_at_ms_ = 0;
  bool link_seen_ = false;
};

}  // namespace kenpi

#endif  // KENPI_POWER_STATE_MACHINE_H_

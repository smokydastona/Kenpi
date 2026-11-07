// Host-executable unit tests for `kenpi::VehiclePowerStateMachine` and
// `kenpi::BoardConfig`. These are pure logic tests: no GPIO, no real clock,
// no Arduino/ESP-IDF dependency. Every transition is driven by an explicit
// `now_ms` the test supplies, so timing-dependent behavior (debounce,
// startup/shutdown delays, link-loss detection) is fully deterministic.
#include "kenpi/board_config.h"
#include "kenpi/power_state_machine.h"

#include <cstdio>

namespace {

int g_failures = 0;

#define CHECK(cond)                                                          \
  do {                                                                       \
    if (!(cond)) {                                                           \
      std::fprintf(stderr, "FAIL: %s (%s:%d)\n", #cond, __FILE__, __LINE__); \
      ++g_failures;                                                          \
    }                                                                        \
  } while (0)

kenpi::PowerStateMachineConfig TestConfig() {
  kenpi::PowerStateMachineConfig config;
  config.ignition_debounce_ms = 100;
  config.startup_delay_ms = 200;
  config.shutdown_delay_ms = 300;
  config.shutdown_ack_timeout_ms = 400;
  config.link_loss_timeout_ms = 500;
  return config;
}

// Drives `machine` from a fresh kVehicleOff state through to kRunning and
// returns the timestamp (ms) at which it entered kRunning, so callers can
// continue driving the machine from a known-good baseline.
std::uint32_t AdvanceToRunning(kenpi::VehiclePowerStateMachine* machine,
                                const kenpi::PowerStateMachineConfig& config) {
  machine->Update(/*ignition_raw=*/true, /*link_ok=*/false, /*brownout=*/false, 0);
  CHECK(machine->state() == kenpi::PowerState::kVehicleOff);

  const std::uint32_t debounced_at = config.ignition_debounce_ms;
  machine->Update(true, false, false, debounced_at);
  CHECK(machine->state() == kenpi::PowerState::kIgnitionDetected);

  const std::uint32_t started_at = debounced_at + config.startup_delay_ms;
  machine->Update(true, false, false, started_at);
  CHECK(machine->state() == kenpi::PowerState::kStarting);

  const std::uint32_t running_at = started_at + 1;
  machine->Update(true, true, false, running_at);
  CHECK(machine->state() == kenpi::PowerState::kRunning);
  return running_at;
}

void TestRejectsIncompleteConfig() {
  kenpi::PowerStateMachineConfig config;  // All-zero: invalid.
  kenpi::VehiclePowerStateMachine machine;
  const char* error = nullptr;
  CHECK(!kenpi::VehiclePowerStateMachine::Create(config, 0, &machine, &error));
  CHECK(error != nullptr);
}

void TestFullStartupToRunningSequence() {
  auto config = TestConfig();
  kenpi::VehiclePowerStateMachine machine;
  const char* error = nullptr;
  CHECK(kenpi::VehiclePowerStateMachine::Create(config, 0, &machine, &error));
  CHECK(machine.state() == kenpi::PowerState::kVehicleOff);
  AdvanceToRunning(&machine, config);
}

void TestIgnitionBlipDuringCrankDoesNotShutDown() {
  auto config = TestConfig();
  kenpi::VehiclePowerStateMachine machine;
  const char* error = nullptr;
  CHECK(kenpi::VehiclePowerStateMachine::Create(config, 0, &machine, &error));
  const std::uint32_t running_at = AdvanceToRunning(&machine, config);

  // Brief ignition dropout (e.g. engine restart crank). The raw signal must
  // first establish a new debounce candidate, then hold for the debounce
  // window, before the machine treats it as a real change.
  const std::uint32_t drop_candidate_at = running_at + 10;
  machine.Update(false, true, false, drop_candidate_at);
  CHECK(machine.state() == kenpi::PowerState::kRunning);  // Not yet debounced.
  const std::uint32_t lost_at = drop_candidate_at + config.ignition_debounce_ms;
  machine.Update(false, true, false, lost_at);
  CHECK(machine.state() == kenpi::PowerState::kIgnitionLost);

  // Ignition returns well within the shutdown grace period.
  const std::uint32_t return_candidate_at = lost_at + 10;
  machine.Update(true, true, false, return_candidate_at);
  const std::uint32_t recovered_at = return_candidate_at + config.ignition_debounce_ms;
  machine.Update(true, true, false, recovered_at);
  CHECK(machine.state() == kenpi::PowerState::kRunning);
}

void TestSustainedIgnitionLossLeadsToShutdownSequence() {
  auto config = TestConfig();
  kenpi::VehiclePowerStateMachine machine;
  const char* error = nullptr;
  CHECK(kenpi::VehiclePowerStateMachine::Create(config, 0, &machine, &error));
  const std::uint32_t running_at = AdvanceToRunning(&machine, config);

  const std::uint32_t drop_candidate_at = running_at + 10;
  machine.Update(false, true, false, drop_candidate_at);
  const std::uint32_t lost_at = drop_candidate_at + config.ignition_debounce_ms;
  machine.Update(false, true, false, lost_at);  // -> IGNITION_LOST.
  CHECK(machine.state() == kenpi::PowerState::kIgnitionLost);

  const std::uint32_t shutdown_pending_at = lost_at + config.shutdown_delay_ms;
  machine.Update(false, true, false, shutdown_pending_at);
  CHECK(machine.state() == kenpi::PowerState::kShutdownPending);

  // Pi's link drops as it powers off.
  const std::uint32_t shutting_down_at = shutdown_pending_at + 10;
  machine.Update(false, false, false, shutting_down_at);
  CHECK(machine.state() == kenpi::PowerState::kShuttingDown);

  const std::uint32_t sleeping_at = shutting_down_at + config.shutdown_ack_timeout_ms;
  machine.Update(false, false, false, sleeping_at);
  CHECK(machine.state() == kenpi::PowerState::kSleeping);

  // Ignition returns while sleeping: wake sequence begins. The raw signal
  // must first establish a new debounce candidate before it can latch.
  const std::uint32_t wake_candidate_at = sleeping_at + 10;
  machine.Update(true, false, false, wake_candidate_at);
  CHECK(machine.state() == kenpi::PowerState::kSleeping);  // Not yet debounced.
  const std::uint32_t woken_at = wake_candidate_at + config.ignition_debounce_ms;
  machine.Update(true, false, false, woken_at);
  CHECK(machine.state() == kenpi::PowerState::kWaking);
}

void TestBrownoutForcesFaultFromAnyState() {
  auto config = TestConfig();
  kenpi::VehiclePowerStateMachine machine;
  const char* error = nullptr;
  CHECK(kenpi::VehiclePowerStateMachine::Create(config, 0, &machine, &error));
  const std::uint32_t running_at = AdvanceToRunning(&machine, config);

  machine.Update(true, true, /*brownout=*/true, running_at + 10);
  CHECK(machine.state() == kenpi::PowerState::kFault);

  // Fault clears while ignition is still on: re-enter the startup sequence
  // rather than silently resuming RUNNING.
  machine.Update(true, true, false, running_at + 20);
  CHECK(machine.state() == kenpi::PowerState::kIgnitionDetected);
}

void TestLinkLossWhileRunningRaisesFaultWithoutAffectingIgnition() {
  auto config = TestConfig();
  kenpi::VehiclePowerStateMachine machine;
  const char* error = nullptr;
  CHECK(kenpi::VehiclePowerStateMachine::Create(config, 0, &machine, &error));
  const std::uint32_t running_at = AdvanceToRunning(&machine, config);

  machine.Update(true, false, false, running_at + config.link_loss_timeout_ms);
  CHECK(machine.state() == kenpi::PowerState::kFault);
}

void TestBoardConfigRejectsUnconfiguredPins() {
  kenpi::BoardConfig config;
  const char* error = nullptr;
  CHECK(!config.IsValid(&error));
  CHECK(error != nullptr);
}

void TestBoardConfigRejectsCollidingPins() {
  kenpi::BoardConfig config;
  config.max_valid_gpio = 39;
  config.ignition_sense_pin = 4;
  config.uart_tx_pin = 4;  // Collides with ignition_sense_pin.
  config.uart_rx_pin = 5;
  const char* error = nullptr;
  CHECK(!config.IsValid(&error));
}

void TestBoardConfigAcceptsCompleteNonCollidingConfig() {
  kenpi::BoardConfig config;
  config.max_valid_gpio = 39;
  config.ignition_sense_pin = 4;
  config.uart_tx_pin = 17;
  config.uart_rx_pin = 18;
  const char* error = nullptr;
  CHECK(config.IsValid(&error));
}

}  // namespace

int main() {
  TestRejectsIncompleteConfig();
  TestFullStartupToRunningSequence();
  TestIgnitionBlipDuringCrankDoesNotShutDown();
  TestSustainedIgnitionLossLeadsToShutdownSequence();
  TestBrownoutForcesFaultFromAnyState();
  TestLinkLossWhileRunningRaisesFaultWithoutAffectingIgnition();
  TestBoardConfigRejectsUnconfiguredPins();
  TestBoardConfigRejectsCollidingPins();
  TestBoardConfigAcceptsCompleteNonCollidingConfig();

  if (g_failures == 0) {
    std::printf("test_power_state_machine: all checks passed\n");
    return 0;
  }
  std::fprintf(stderr, "test_power_state_machine: %d check(s) failed\n", g_failures);
  return 1;
}

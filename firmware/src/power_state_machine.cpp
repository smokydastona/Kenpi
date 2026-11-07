#include "kenpi/power_state_machine.h"

namespace kenpi {

const char* ToString(PowerState state) {
  switch (state) {
    case PowerState::kVehicleOff:
      return "VEHICLE_OFF";
    case PowerState::kIgnitionDetected:
      return "IGNITION_DETECTED";
    case PowerState::kStarting:
      return "STARTING";
    case PowerState::kRunning:
      return "RUNNING";
    case PowerState::kIgnitionLost:
      return "IGNITION_LOST";
    case PowerState::kShutdownPending:
      return "SHUTDOWN_PENDING";
    case PowerState::kShuttingDown:
      return "SHUTTING_DOWN";
    case PowerState::kSleeping:
      return "SLEEPING";
    case PowerState::kWaking:
      return "WAKING";
    case PowerState::kFault:
      return "FAULT";
  }
  return "UNKNOWN";
}

bool PowerStateMachineConfig::IsValid(const char** error) const {
  if (ignition_debounce_ms == 0) {
    if (error) *error = "ignition_debounce_ms must be greater than zero";
    return false;
  }
  if (startup_delay_ms == 0) {
    if (error) *error = "startup_delay_ms must be greater than zero";
    return false;
  }
  if (shutdown_delay_ms == 0) {
    if (error) *error = "shutdown_delay_ms must be greater than zero";
    return false;
  }
  if (shutdown_ack_timeout_ms == 0) {
    if (error) *error = "shutdown_ack_timeout_ms must be greater than zero";
    return false;
  }
  if (link_loss_timeout_ms == 0) {
    if (error) *error = "link_loss_timeout_ms must be greater than zero";
    return false;
  }
  return true;
}

bool VehiclePowerStateMachine::Create(const PowerStateMachineConfig& config,
                                       std::uint32_t now_ms, VehiclePowerStateMachine* out,
                                       const char** error) {
  if (!config.IsValid(error)) {
    return false;
  }
  if (out == nullptr) {
    if (error) *error = "output pointer is null";
    return false;
  }
  out->config_ = config;
  out->state_ = PowerState::kVehicleOff;
  out->state_entered_at_ms_ = now_ms;
  out->debounced_ignition_ = false;
  out->raw_ignition_candidate_ = false;
  out->raw_change_at_ms_ = now_ms;
  out->last_link_ok_at_ms_ = now_ms;
  out->link_seen_ = false;
  return true;
}

void VehiclePowerStateMachine::EnterState(PowerState next, std::uint32_t now_ms) {
  state_ = next;
  state_entered_at_ms_ = now_ms;
}

std::uint32_t VehiclePowerStateMachine::TimeInState(std::uint32_t now_ms) const {
  // Unsigned subtraction is wraparound-safe for a monotonic ms counter.
  return static_cast<std::uint32_t>(now_ms - state_entered_at_ms_);
}

void VehiclePowerStateMachine::UpdateDebounce(bool ignition_raw, std::uint32_t now_ms) {
  if (ignition_raw != raw_ignition_candidate_) {
    raw_ignition_candidate_ = ignition_raw;
    raw_change_at_ms_ = now_ms;
  }
  if (raw_ignition_candidate_ != debounced_ignition_ &&
      static_cast<std::uint32_t>(now_ms - raw_change_at_ms_) >= config_.ignition_debounce_ms) {
    debounced_ignition_ = raw_ignition_candidate_;
  }
}

PowerState VehiclePowerStateMachine::Update(bool ignition_raw, bool link_ok, bool brownout,
                                             std::uint32_t now_ms) {
  UpdateDebounce(ignition_raw, now_ms);
  if (link_ok) {
    link_seen_ = true;
    last_link_ok_at_ms_ = now_ms;
  }

  if (brownout && state_ != PowerState::kFault) {
    EnterState(PowerState::kFault, now_ms);
    return state_;
  }

  switch (state_) {
    case PowerState::kVehicleOff:
      if (debounced_ignition_) {
        EnterState(PowerState::kIgnitionDetected, now_ms);
      }
      break;

    case PowerState::kIgnitionDetected:
      if (!debounced_ignition_) {
        EnterState(PowerState::kVehicleOff, now_ms);
      } else if (TimeInState(now_ms) >= config_.startup_delay_ms) {
        EnterState(PowerState::kStarting, now_ms);
      }
      break;

    case PowerState::kStarting:
      if (!debounced_ignition_) {
        EnterState(PowerState::kIgnitionLost, now_ms);
      } else if (link_ok) {
        EnterState(PowerState::kRunning, now_ms);
      }
      break;

    case PowerState::kRunning:
      if (!debounced_ignition_) {
        EnterState(PowerState::kIgnitionLost, now_ms);
      } else if (link_seen_ && !link_ok &&
                 static_cast<std::uint32_t>(now_ms - last_link_ok_at_ms_) >=
                     config_.link_loss_timeout_ms) {
        EnterState(PowerState::kFault, now_ms);
      }
      break;

    case PowerState::kIgnitionLost:
      if (debounced_ignition_) {
        EnterState(PowerState::kRunning, now_ms);
      } else if (TimeInState(now_ms) >= config_.shutdown_delay_ms) {
        EnterState(PowerState::kShutdownPending, now_ms);
      }
      break;

    case PowerState::kShutdownPending:
      if (debounced_ignition_) {
        EnterState(PowerState::kRunning, now_ms);
      } else if (!link_ok || TimeInState(now_ms) >= config_.shutdown_ack_timeout_ms) {
        EnterState(PowerState::kShuttingDown, now_ms);
      }
      break;

    case PowerState::kShuttingDown:
      if (!link_ok || TimeInState(now_ms) >= config_.shutdown_ack_timeout_ms) {
        EnterState(PowerState::kSleeping, now_ms);
      }
      break;

    case PowerState::kSleeping:
      if (debounced_ignition_) {
        EnterState(PowerState::kWaking, now_ms);
      }
      break;

    case PowerState::kWaking:
      if (!debounced_ignition_) {
        EnterState(PowerState::kVehicleOff, now_ms);
      } else if (TimeInState(now_ms) >= config_.startup_delay_ms) {
        EnterState(PowerState::kStarting, now_ms);
      }
      break;

    case PowerState::kFault:
      if (!brownout) {
        EnterState(
            debounced_ignition_ ? PowerState::kIgnitionDetected : PowerState::kVehicleOff,
            now_ms);
      }
      break;
  }

  return state_;
}

}  // namespace kenpi

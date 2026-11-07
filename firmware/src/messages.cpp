#include "kenpi/messages.h"

namespace kenpi {

namespace {

VehicleMessage Base(MessageType type, std::uint16_t sequence) {
  VehicleMessage message;
  message.message_type = static_cast<std::uint8_t>(type);
  message.sequence = sequence;
  return message;
}

}  // namespace

VehicleMessage MakeHeartbeat(std::uint16_t sequence, std::uint32_t uptime_ms) {
  VehicleMessage message = Base(MessageType::kHeartbeat, sequence);
  for (int shift = 24; shift >= 0; shift -= 8) {
    message.payload.push_back(static_cast<std::uint8_t>((uptime_ms >> shift) & 0xFF));
  }
  return message;
}

VehicleMessage MakePowerState(std::uint16_t sequence, PowerState state, bool ignition_on) {
  VehicleMessage message = Base(MessageType::kPowerState, sequence);
  message.payload.push_back(static_cast<std::uint8_t>(state));
  message.payload.push_back(ignition_on ? kFlagIgnitionOn : 0);
  return message;
}

VehicleMessage MakeShutdownRequest(std::uint16_t sequence, std::uint16_t grace_seconds) {
  VehicleMessage message = Base(MessageType::kShutdownRequest, sequence);
  message.payload.push_back(static_cast<std::uint8_t>(grace_seconds >> 8));
  message.payload.push_back(static_cast<std::uint8_t>(grace_seconds & 0xFF));
  return message;
}

VehicleMessage MakeShutdownAck(std::uint16_t sequence) {
  return Base(MessageType::kShutdownAck, sequence);
}

bool IsShutdownAck(const VehicleMessage& message) {
  return message.message_type == static_cast<std::uint8_t>(MessageType::kShutdownAck) &&
         message.payload.empty();
}

bool ParsePowerState(const VehicleMessage& message, PowerState* state, bool* ignition_on) {
  if (message.message_type != static_cast<std::uint8_t>(MessageType::kPowerState) ||
      message.payload.size() != 2) {
    return false;
  }
  if (message.payload[0] > static_cast<std::uint8_t>(PowerState::kFault) ||
      (message.payload[1] & ~kFlagIgnitionOn) != 0) {
    return false;
  }
  *state = static_cast<PowerState>(message.payload[0]);
  *ignition_on = (message.payload[1] & kFlagIgnitionOn) != 0;
  return true;
}

}  // namespace kenpi

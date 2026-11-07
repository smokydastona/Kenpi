// Typed vehicle-link messages carried in `kenpi::VehicleMessage` frames.
// Mirrors `kenpi/vehicle/messages.py`; shared fixtures in
// `protocol/vehicle_frames.txt` keep the two in lock-step. No message
// commands dashcam power.
#ifndef KENPI_MESSAGES_H_
#define KENPI_MESSAGES_H_

#include <cstdint>

#include "kenpi/power_state_machine.h"
#include "kenpi/protocol.h"

namespace kenpi {

enum class MessageType : std::uint8_t {
  kHeartbeat = 0x01,
  kPowerState = 0x02,
  kShutdownRequest = 0x03,
  kShutdownAck = 0x04,
};

inline constexpr std::uint8_t kFlagIgnitionOn = 0x01;

VehicleMessage MakeHeartbeat(std::uint16_t sequence, std::uint32_t uptime_ms);
VehicleMessage MakePowerState(std::uint16_t sequence, PowerState state, bool ignition_on);
VehicleMessage MakeShutdownRequest(std::uint16_t sequence, std::uint16_t grace_seconds);
VehicleMessage MakeShutdownAck(std::uint16_t sequence);

// Returns true only when `message` is a well-formed ShutdownAck.
bool IsShutdownAck(const VehicleMessage& message);

// Returns true only when `message` is a well-formed PowerState report.
bool ParsePowerState(const VehicleMessage& message, PowerState* state, bool* ignition_on);

}  // namespace kenpi

#endif  // KENPI_MESSAGES_H_

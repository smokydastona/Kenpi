// Typed vehicle-link messages carried in `kenpi::VehicleMessage` frames.
// Mirrors `kenpi/vehicle/messages.py`; shared fixtures in
// `protocol/vehicle_frames.txt` keep the two in lock-step. No message
// commands dashcam power.
#ifndef KENPI_MESSAGES_H_
#define KENPI_MESSAGES_H_

#include <cstddef>
#include <cstdint>
#include <vector>

#include "kenpi/power_state_machine.h"
#include "kenpi/protocol.h"

namespace kenpi {

enum class MessageType : std::uint8_t {
  kHeartbeat = 0x01,
  kPowerState = 0x02,
  kShutdownRequest = 0x03,
  kShutdownAck = 0x04,
  kDiagRequest = 0x05,
  kDiagResponse = 0x06,
};

enum class DiagnosticStatus : std::uint8_t {
  kOk = 0,
  kTimeout = 1,
  kNegativeResponse = 2,
  kBusUnavailable = 3,
  kRejected = 4,
};

inline constexpr std::size_t kMaxDiagData = 32;

inline constexpr std::uint8_t kFlagIgnitionOn = 0x01;

VehicleMessage MakeHeartbeat(std::uint16_t sequence, std::uint32_t uptime_ms);
VehicleMessage MakePowerState(std::uint16_t sequence, PowerState state, bool ignition_on);
VehicleMessage MakeShutdownRequest(std::uint16_t sequence, std::uint16_t grace_seconds);
VehicleMessage MakeShutdownAck(std::uint16_t sequence);

// Read-only OBD-II services only: 01, 02, 03, 09.
bool IsReadOnlyService(std::uint8_t service);

VehicleMessage MakeDiagnosticResponse(std::uint16_t sequence, std::uint16_t request_id,
                                      DiagnosticStatus status,
                                      const std::vector<std::uint8_t>& data);

// Parses a Pi -> controller request. Returns false for any malformed request
// or any service that is not read-only, so the controller never relays it.
// `has_pid` is false for service 03.
bool ParseDiagnosticRequest(const VehicleMessage& message, std::uint16_t* request_id,
                            std::uint8_t* service, bool* has_pid, std::uint8_t* pid);

// Returns true only when `message` is a well-formed ShutdownAck.
bool IsShutdownAck(const VehicleMessage& message);

// Returns true only when `message` is a well-formed PowerState report.
bool ParsePowerState(const VehicleMessage& message, PowerState* state, bool* ignition_on);

}  // namespace kenpi

#endif  // KENPI_MESSAGES_H_

// Verifies the firmware encoder/decoder and typed messages against the
// shared fixtures in `protocol/vehicle_frames.txt`, which the Python test
// suite checks against `kenpi.vehicle` as well.
#include <cstdio>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

#include "kenpi/messages.h"
#include "kenpi/protocol.h"

namespace {

int g_failures = 0;

#define CHECK(cond)                                                          \
  do {                                                                       \
    if (!(cond)) {                                                           \
      std::fprintf(stderr, "FAIL: %s (%s:%d)\n", #cond, __FILE__, __LINE__); \
      ++g_failures;                                                          \
    }                                                                        \
  } while (0)

std::vector<std::uint8_t> FromHex(const std::string& hex) {
  std::vector<std::uint8_t> out;
  for (std::size_t i = 0; i + 1 < hex.size(); i += 2) {
    out.push_back(static_cast<std::uint8_t>(std::stoi(hex.substr(i, 2), nullptr, 16)));
  }
  return out;
}

std::vector<std::string> Split(const std::string& line) {
  std::vector<std::string> fields;
  std::stringstream stream(line);
  std::string field;
  while (std::getline(stream, field, '|')) fields.push_back(field);
  if (!line.empty() && line.back() == '|') fields.emplace_back();
  return fields;
}

kenpi::VehicleMessage Typed(const std::string& name, std::uint16_t sequence) {
  if (name == "heartbeat") return kenpi::MakeHeartbeat(sequence, 123456);
  if (name == "power_running") {
    return kenpi::MakePowerState(sequence, kenpi::PowerState::kRunning, true);
  }
  if (name == "power_fault_no_ignition") {
    return kenpi::MakePowerState(sequence, kenpi::PowerState::kFault, false);
  }
  if (name == "shutdown_request") return kenpi::MakeShutdownRequest(sequence, 30);
  return kenpi::MakeShutdownAck(sequence);
}

}  // namespace

int main(int argc, char** argv) {
  if (argc != 2) {
    std::fprintf(stderr, "usage: test_shared_fixtures <vehicle_frames.txt>\n");
    return 2;
  }
  std::ifstream input(argv[1]);
  CHECK(input.is_open());
  std::string line;
  int cases = 0;
  while (std::getline(input, line)) {
    if (line.empty() || line[0] == '#') continue;
    const auto fields = Split(line);
    CHECK(fields.size() == 5);
    if (fields.size() != 5) continue;
    ++cases;
    const auto sequence = static_cast<std::uint16_t>(std::stoi(fields[2]));
    const auto frame = FromHex(fields[4]);

    const kenpi::VehicleMessage built = Typed(fields[0], sequence);
    CHECK(built.message_type == std::stoi(fields[1]));
    CHECK(built.payload == FromHex(fields[3]));
    CHECK(kenpi::EncodeMessage(built) == frame);

    kenpi::VehicleMessageDecoder decoder;
    const auto decoded = decoder.Feed(frame.data(), frame.size());
    CHECK(decoded.size() == 1);
    if (decoded.size() == 1) {
      CHECK(decoded[0].sequence == sequence);
      CHECK(decoded[0].payload == FromHex(fields[3]));
    }
  }
  CHECK(cases == 5);

  kenpi::VehicleMessage bad = kenpi::MakePowerState(1, kenpi::PowerState::kRunning, true);
  bad.payload[0] = 10;
  kenpi::PowerState state;
  bool ignition = false;
  CHECK(!kenpi::ParsePowerState(bad, &state, &ignition));
  CHECK(kenpi::ParsePowerState(
      kenpi::MakePowerState(1, kenpi::PowerState::kSleeping, true), &state, &ignition));
  CHECK(state == kenpi::PowerState::kSleeping && ignition);
  CHECK(kenpi::IsShutdownAck(kenpi::MakeShutdownAck(9)));
  CHECK(!kenpi::IsShutdownAck(kenpi::MakeHeartbeat(9, 1)));

  if (g_failures == 0) {
    std::printf("test_shared_fixtures: all checks passed\n");
    return 0;
  }
  return 1;
}

// Host-executable unit tests for `kenpi::protocol`. No Arduino/ESP-IDF
// dependency: compiles and runs with a plain C++17 toolchain (see
// `firmware/tools/run_native_tests.sh`), so these checks can be verified in
// any environment without physical hardware or a device-specific toolchain.
#include "kenpi/protocol.h"

#include <cstdio>
#include <cstdlib>
#include <vector>

namespace {

int g_failures = 0;

#define CHECK(cond)                                                              \
  do {                                                                           \
    if (!(cond)) {                                                               \
      std::fprintf(stderr, "FAIL: %s (%s:%d)\n", #cond, __FILE__, __LINE__);     \
      ++g_failures;                                                              \
    }                                                                            \
  } while (0)

void TestEncodeDecodeRoundTrip() {
  kenpi::VehicleMessage message{42, 7, {1, 2, 3, 4}, kenpi::kProtocolVersion};
  const char* error = nullptr;
  CHECK(kenpi::ValidateMessage(message, &error));
  const std::vector<std::uint8_t> frame = kenpi::EncodeMessage(message);

  kenpi::VehicleMessageDecoder decoder;
  const auto decoded = decoder.Feed(frame.data(), frame.size());
  CHECK(decoded.size() == 1);
  if (decoded.size() == 1) {
    CHECK(decoded[0].message_type == 42);
    CHECK(decoded[0].sequence == 7);
    CHECK(decoded[0].payload.size() == 4);
    CHECK(decoded[0].version == kenpi::kProtocolVersion);
  }
}

void TestDecoderHandlesFragmentedReads() {
  kenpi::VehicleMessage message{1, 100, {9, 9}, kenpi::kProtocolVersion};
  const std::vector<std::uint8_t> frame = kenpi::EncodeMessage(message);

  kenpi::VehicleMessageDecoder decoder;
  std::vector<kenpi::VehicleMessage> all;
  for (std::size_t i = 0; i < frame.size(); ++i) {
    auto chunk = decoder.Feed(&frame[i], 1);
    all.insert(all.end(), chunk.begin(), chunk.end());
  }
  CHECK(all.size() == 1);
}

void TestDecoderRejectsBadCrc() {
  kenpi::VehicleMessage message{1, 1, {1}, kenpi::kProtocolVersion};
  std::vector<std::uint8_t> frame = kenpi::EncodeMessage(message);
  frame.back() ^= 0xFF;  // Corrupt the CRC.

  kenpi::VehicleMessageDecoder decoder;
  const auto decoded = decoder.Feed(frame.data(), frame.size());
  CHECK(decoded.empty());
}

void TestDecoderResynchronizesAfterGarbage() {
  kenpi::VehicleMessage message{2, 2, {5, 6}, kenpi::kProtocolVersion};
  std::vector<std::uint8_t> frame = kenpi::EncodeMessage(message);
  std::vector<std::uint8_t> input = {0xDE, 0xAD, 0xBE, 0xEF};
  input.insert(input.end(), frame.begin(), frame.end());

  kenpi::VehicleMessageDecoder decoder;
  const auto decoded = decoder.Feed(input.data(), input.size());
  CHECK(decoded.size() == 1);
}

void TestDecoderRejectsOversizedPayloadLength() {
  // Hand-craft a header claiming a payload larger than the protocol limit.
  std::vector<std::uint8_t> input = {
      'K', 'P', kenpi::kProtocolVersion, 0, 0, 0, 0xFF, 0xFF,
  };
  kenpi::VehicleMessageDecoder decoder;
  const auto decoded = decoder.Feed(input.data(), input.size());
  CHECK(decoded.empty());
}

void TestValidateMessageRejectsOversizedPayload() {
  kenpi::VehicleMessage message;
  message.payload.resize(kenpi::kMaxPayloadLength + 1);
  const char* error = nullptr;
  CHECK(!kenpi::ValidateMessage(message, &error));
  CHECK(error != nullptr);
}

void TestValidateMessageRejectsUnsupportedVersion() {
  kenpi::VehicleMessage message;
  message.version = kenpi::kProtocolVersion + 1;
  const char* error = nullptr;
  CHECK(!kenpi::ValidateMessage(message, &error));
}

void TestSequenceTrackerAcceptsMonotonicSequence() {
  kenpi::SequenceTracker tracker;
  CHECK(tracker.Accept(10));
  CHECK(tracker.Accept(11));
  CHECK(tracker.Accept(500));
}

void TestSequenceTrackerRejectsDuplicateAndStale() {
  kenpi::SequenceTracker tracker;
  CHECK(tracker.Accept(10));
  CHECK(!tracker.Accept(10));  // Duplicate.
  CHECK(!tracker.Accept(5));   // Stale.
  CHECK(tracker.Accept(20));
}

void TestSequenceTrackerAcceptsWraparound() {
  kenpi::SequenceTracker tracker;
  CHECK(tracker.Accept(0xFFFE));
  CHECK(tracker.Accept(0xFFFF));
  CHECK(tracker.Accept(0));  // Natural 16-bit wraparound.
  CHECK(tracker.Accept(1));
}

}  // namespace

int main() {
  TestEncodeDecodeRoundTrip();
  TestDecoderHandlesFragmentedReads();
  TestDecoderRejectsBadCrc();
  TestDecoderResynchronizesAfterGarbage();
  TestDecoderRejectsOversizedPayloadLength();
  TestValidateMessageRejectsOversizedPayload();
  TestValidateMessageRejectsUnsupportedVersion();
  TestSequenceTrackerAcceptsMonotonicSequence();
  TestSequenceTrackerRejectsDuplicateAndStale();
  TestSequenceTrackerAcceptsWraparound();

  if (g_failures == 0) {
    std::printf("test_protocol: all checks passed\n");
    return 0;
  }
  std::fprintf(stderr, "test_protocol: %d check(s) failed\n", g_failures);
  return 1;
}

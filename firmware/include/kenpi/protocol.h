// Framed UART protocol primitives for the independent auxiliary controller.
//
// This is a byte-for-byte C++ port of `kenpi/vehicle/protocol.py`. Both
// implementations must stay in lock-step: the frame layout, CRC algorithm,
// and sequence-tracking rules are duplicated intentionally so the Pi side
// and the auxiliary controller side cannot silently drift apart. Any change
// here must be mirrored in the Python module (and vice versa), and the
// native unit tests in `firmware/test/test_protocol.cpp` encode the shared
// test vectors that both sides are expected to satisfy.
//
// This header has no Arduino/ESP-IDF dependency. It compiles with any
// hosted C++17 toolchain (see `firmware/tools/run_native_tests.sh`) so the
// protocol logic can be verified without physical hardware or a
// device-specific toolchain.
#ifndef KENPI_PROTOCOL_H_
#define KENPI_PROTOCOL_H_

#include <cstddef>
#include <cstdint>
#include <optional>
#include <vector>

namespace kenpi {

inline constexpr std::uint8_t kProtocolMagic[2] = {'K', 'P'};
inline constexpr std::uint8_t kProtocolVersion = 1;
inline constexpr std::size_t kMaxPayloadLength = 512;
inline constexpr std::size_t kHeaderSize = 8;
inline constexpr std::size_t kCrcSize = 2;

// CRC-16/CCITT-FALSE: initial value 0xFFFF, polynomial 0x1021, MSB-first,
// no final XOR. Matches `kenpi.vehicle.protocol._crc16` exactly.
std::uint16_t Crc16(const std::uint8_t* data, std::size_t length);

// A single decoded/encodable protocol message.
struct VehicleMessage {
  std::uint8_t message_type = 0;
  std::uint16_t sequence = 0;
  std::vector<std::uint8_t> payload;
  std::uint8_t version = kProtocolVersion;
};

// Validates field ranges the same way the Python dataclass's
// `__post_init__` does. Returns false (and leaves `error` populated) when
// the message cannot be encoded safely.
bool ValidateMessage(const VehicleMessage& message, const char** error);

// Serializes `message` into a framed, CRC-protected byte string. The caller
// must have validated the message first (e.g. via `ValidateMessage`);
// this function does not re-validate so it can be used in hot paths where
// the message was already constructed from trusted, validated data.
std::vector<std::uint8_t> EncodeMessage(const VehicleMessage& message);

// Incremental decoder tolerating partial input and resynchronizing on the
// magic bytes, mirroring `kenpi.vehicle.protocol.VehicleMessageDecoder`.
class VehicleMessageDecoder {
 public:
  // Appends `chunk` to the internal buffer and returns any complete,
  // CRC-valid messages found so far. Unsupported protocol versions are
  // silently skipped (consistent with the Python implementation), and
  // corrupt bytes are discarded one at a time while resynchronizing on the
  // next occurrence of the magic bytes.
  std::vector<VehicleMessage> Feed(const std::uint8_t* chunk, std::size_t length);

 private:
  std::vector<std::uint8_t> buffer_;
};

// Rejects duplicate or stale sequence numbers using modular (wraparound)
// ordering, mirroring `kenpi.vehicle.protocol.SequenceTracker`.
class SequenceTracker {
 public:
  // Returns true if `sequence` is newer than the last accepted sequence
  // (or if no sequence has been accepted yet), and records it as the new
  // high-water mark. Returns false for duplicates/replays/stale values.
  bool Accept(std::uint16_t sequence);

  bool has_last() const { return last_.has_value(); }
  std::uint16_t last() const { return *last_; }

 private:
  std::optional<std::uint16_t> last_;
};

}  // namespace kenpi

#endif  // KENPI_PROTOCOL_H_

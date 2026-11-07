#include "kenpi/protocol.h"

#include <algorithm>

namespace kenpi {

std::uint16_t Crc16(const std::uint8_t* data, std::size_t length) {
  std::uint16_t crc = 0xFFFF;
  for (std::size_t i = 0; i < length; ++i) {
    crc = static_cast<std::uint16_t>(crc ^ (static_cast<std::uint16_t>(data[i]) << 8));
    for (int bit = 0; bit < 8; ++bit) {
      if (crc & 0x8000) {
        crc = static_cast<std::uint16_t>((crc << 1) ^ 0x1021);
      } else {
        crc = static_cast<std::uint16_t>(crc << 1);
      }
    }
  }
  return crc;
}

bool ValidateMessage(const VehicleMessage& message, const char** error) {
  if (message.version != kProtocolVersion) {
    if (error) *error = "unsupported protocol version";
    return false;
  }
  if (message.payload.size() > kMaxPayloadLength) {
    if (error) *error = "payload exceeds protocol limit";
    return false;
  }
  // message_type and sequence are already range-limited by their fixed-width
  // types (uint8_t/uint16_t), unlike the Python implementation which has to
  // check this explicitly because Python ints are unbounded.
  return true;
}

std::vector<std::uint8_t> EncodeMessage(const VehicleMessage& message) {
  std::vector<std::uint8_t> frame;
  frame.reserve(kHeaderSize + message.payload.size() + kCrcSize);
  frame.push_back(kProtocolMagic[0]);
  frame.push_back(kProtocolMagic[1]);
  frame.push_back(message.version);
  frame.push_back(message.message_type);
  frame.push_back(static_cast<std::uint8_t>(message.sequence >> 8));
  frame.push_back(static_cast<std::uint8_t>(message.sequence & 0xFF));
  const auto payload_length = static_cast<std::uint16_t>(message.payload.size());
  frame.push_back(static_cast<std::uint8_t>(payload_length >> 8));
  frame.push_back(static_cast<std::uint8_t>(payload_length & 0xFF));
  frame.insert(frame.end(), message.payload.begin(), message.payload.end());
  const std::uint16_t crc = Crc16(frame.data(), frame.size());
  frame.push_back(static_cast<std::uint8_t>(crc >> 8));
  frame.push_back(static_cast<std::uint8_t>(crc & 0xFF));
  return frame;
}

std::vector<VehicleMessage> VehicleMessageDecoder::Feed(const std::uint8_t* chunk,
                                                         std::size_t length) {
  buffer_.insert(buffer_.end(), chunk, chunk + length);
  std::vector<VehicleMessage> messages;

  for (;;) {
    const auto start = std::search(buffer_.begin(), buffer_.end(), std::begin(kProtocolMagic),
                                    std::end(kProtocolMagic));
    if (start == buffer_.end()) {
      // No full magic sequence present. Preserve a single trailing byte if
      // it could be the first byte of a magic sequence split across reads,
      // matching the Python decoder's resynchronization behavior.
      if (!buffer_.empty() && buffer_.back() == kProtocolMagic[0]) {
        buffer_ = {buffer_.back()};
      } else {
        buffer_.clear();
      }
      break;
    }
    if (start != buffer_.begin()) {
      buffer_.erase(buffer_.begin(), start);
    }
    if (buffer_.size() < kHeaderSize) {
      break;
    }
    const std::uint8_t version = buffer_[2];
    const std::uint8_t message_type = buffer_[3];
    const std::uint16_t sequence =
        static_cast<std::uint16_t>((buffer_[4] << 8) | buffer_[5]);
    const std::uint16_t payload_length =
        static_cast<std::uint16_t>((buffer_[6] << 8) | buffer_[7]);
    if (payload_length > kMaxPayloadLength) {
      buffer_.erase(buffer_.begin());
      continue;
    }
    const std::size_t frame_length = kHeaderSize + payload_length + kCrcSize;
    if (buffer_.size() < frame_length) {
      break;
    }
    const std::uint16_t expected_crc = static_cast<std::uint16_t>(
        (buffer_[frame_length - 2] << 8) | buffer_[frame_length - 1]);
    const std::uint16_t actual_crc = Crc16(buffer_.data(), frame_length - kCrcSize);
    if (actual_crc != expected_crc) {
      buffer_.erase(buffer_.begin());
      continue;
    }
    std::vector<std::uint8_t> payload(buffer_.begin() + kHeaderSize,
                                       buffer_.begin() + kHeaderSize + payload_length);
    buffer_.erase(buffer_.begin(), buffer_.begin() + static_cast<long>(frame_length));
    if (version != kProtocolVersion) {
      continue;
    }
    messages.push_back(VehicleMessage{message_type, sequence, std::move(payload), version});
  }

  return messages;
}

bool SequenceTracker::Accept(std::uint16_t sequence) {
  if (!last_.has_value()) {
    last_ = sequence;
    return true;
  }
  const std::uint16_t delta = static_cast<std::uint16_t>(sequence - *last_);
  if (delta == 0 || delta >= 0x8000) {
    return false;
  }
  last_ = sequence;
  return true;
}

}  // namespace kenpi

// SPDX-License-Identifier: Apache-2.0
// Adapted from xiaomi-sheng-thp StreamReader; see NOTICE.
#include "stream.hpp"
#include <algorithm>
#include <stdexcept>

namespace nvt {
namespace {
uint64_t littleEndian(const uint8_t *bytes, size_t size) {
    uint64_t value = 0;
    for (size_t index = 0; index < size; ++index)
        value |= uint64_t{bytes[index]} << (8 * index);
    return value;
}
} // namespace

void StreamReader::feed(std::span<const uint8_t> bytes, const Consumer &consume) {
    while (!bytes.empty()) {
        const size_t size = std::min(bytes.size(), required_ - used_);
        std::copy_n(bytes.begin(), size, buffer_.begin() + used_);
        used_ += size;
        bytes = bytes.subspan(size);
        if (used_ != required_)
            continue;
        const auto *header = buffer_.data();
        if (required_ == kStreamHeaderSize) {
            const auto frame_length = littleEndian(header + 6, 2);
            if (littleEndian(header, 4) != 0x3150544e ||
                littleEndian(header + 4, 2) != kStreamHeaderSize ||
                frame_length < kPointPrefixSize + 64 || frame_length > kMaxStreamFrame ||
                (littleEndian(header + 26, 2) & ~(kStreamValid | kStreamEpoch)))
                throw std::runtime_error("invalid NTP1 stream header");
            required_ += frame_length;
            continue;
        }
        if (buffer_[kStreamHeaderSize] != 0)
            throw std::runtime_error("invalid NTP1 point-prefix placeholder");
        const auto payload = std::span<const uint8_t>(buffer_.data(), used_)
                                 .subspan(kStreamHeaderSize + kPointPrefixSize);
        const StreamRecord record{
            littleEndian(header + 8, 8), littleEndian(header + 16, 8),
            uint16_t(littleEndian(header + 24, 2)), uint16_t(littleEndian(header + 26, 2)),
            uint32_t(littleEndian(header + 28, 4)), payload};
        consume(record);
        used_ = 0;
        required_ = kStreamHeaderSize;
    }
}

void StreamReader::finish() const {
    if (used_)
        throw std::runtime_error("truncated NTP1 stream record");
}
} // namespace nvt

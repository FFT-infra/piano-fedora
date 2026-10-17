// SPDX-License-Identifier: Apache-2.0
// Adapted from xiaomi-sheng-thp StreamReader; see NOTICE.
#pragma once
#include <array>
#include <cstdint>
#include <functional>
#include <span>

namespace nvt {
constexpr uint16_t kStreamValid = 1;
constexpr uint16_t kStreamEpoch = 2;
constexpr size_t kStreamHeaderSize = 32;
constexpr size_t kPointPrefixSize = 257;
constexpr size_t kMaxStreamFrame = 8191;

struct StreamRecord {
    uint64_t sequence;
    uint64_t timestamp_ns;
    uint16_t checksum;
    uint16_t flags;
    uint32_t length_words;
    std::span<const uint8_t> payload;
};

class StreamReader {
public:
    using Consumer = std::function<void(const StreamRecord &)>;
    void feed(std::span<const uint8_t> bytes, const Consumer &consume);
    void finish() const;

private:
    std::array<uint8_t, kStreamHeaderSize + kMaxStreamFrame> buffer_{};
    size_t used_ = 0;
    size_t required_ = kStreamHeaderSize;
};
} // namespace nvt

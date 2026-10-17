// SPDX-License-Identifier: MIT
#pragma once

#include "nvt_touch_core.hpp"
#include <span>

namespace nvt {

struct Packet {
    std::span<const uint8_t> payload;
    uint16_t counter;
    uint8_t scan_mode;
    uint8_t data_type;
};

struct FingerFrame {
    uint16_t counter;
    uint8_t scan_mode;
    Matrix matrix;
};

// Validate the firmware-defined checksum region; trailing transport padding is retained by the caller.
Packet parsePacket(std::span<const uint8_t> payload);
FingerFrame parseFingerFrame(const Packet &packet);
FingerFrame parseFingerFrame(std::span<const uint8_t> payload);

} // namespace nvt

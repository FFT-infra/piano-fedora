// SPDX-License-Identifier: Apache-2.0
// Adapted from xiaomi-sheng-thp UInputTouch; see NOTICE.
#pragma once
#include "nvt_touch_core.hpp"
#include <linux/input.h>
#include <span>
#include <string>

namespace nvt {
class UInputTouch {
public:
    UInputTouch(int width, int height);
    ~UInputTouch();
    UInputTouch(const UInputTouch &) = delete;
    UInputTouch &operator=(const UInputTouch &) = delete;
    void report(std::span<const Slot> slots);
    std::string sysname() const;

private:
    void checkedIoctl(unsigned long request, unsigned long value);
    int allocateTrackingId();
    int fd_ = -1, maximum_x_, maximum_y_, next_id_ = 0;
    std::array<int, kFingerSlots> active_{}, ids_{};
    std::vector<input_event> events_;
};
} // namespace nvt

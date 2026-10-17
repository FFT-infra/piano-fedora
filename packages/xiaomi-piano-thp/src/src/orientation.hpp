// SPDX-License-Identifier: MIT
#pragma once
#include "nvt_touch_core.hpp"
#include <algorithm>
#include <span>
#include <vector>

namespace nvt {
// Piano panel orientation, from the 2026-10-07 operator-present P18 session
// (mainline-p18-gestures-02-20261007): landscape, USB port on the right, a one-finger
// swipe from the left edge to the right edge increased processor X (electrode
// columns) at constant processor Y, and a swipe from the top edge to the bottom edge
// decreased processor Y (rows) at constant processor X. Processor X therefore spans
// the 3200-pixel width left to right and processor Y the 2136-pixel height with the
// top edge high. The panel reports X = processor X (range 0..column_maximum - 1) and
// Y = reversed processor Y (range 0..row_maximum - 1). The 2026-09-25 P15 corner-order
// reading, which swapped these two outputs, was wrong. Corrections happen here only.
struct PanelAxes {
    int width;
    int height;
};

inline PanelAxes panelAxes(const Calibration &calibration) {
    return {calibration.column_maximum, calibration.row_maximum};
}

inline Contact panelContact(Contact contact, const Calibration &calibration) {
    const int x = std::clamp(contact.x, 0, calibration.column_maximum - 1);
    const int y = std::clamp(contact.y, 0, calibration.row_maximum - 1);
    contact.x = x;
    contact.y = calibration.row_maximum - 1 - y;
    return contact;
}

inline void panelSlots(std::span<const Slot> slots, const Calibration &calibration,
                       std::vector<Slot> &output) {
    output.assign(slots.begin(), slots.end());
    for (Slot &slot : output)
        slot.contact = panelContact(slot.contact, calibration);
}
} // namespace nvt

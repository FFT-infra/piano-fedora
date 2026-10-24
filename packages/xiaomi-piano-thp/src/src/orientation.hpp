// SPDX-License-Identifier: MIT
#pragma once
#include "nvt_touch_core.hpp"
#include "panel.hpp"
#include <algorithm>
#include <span>
#include <vector>

namespace nvt {
// Piano panel orientation, measured with one-finger swipes on the panel in landscape
// with the USB port on the right: a swipe from the left edge to the right edge
// increased processor X (electrode columns) at constant processor Y, and a swipe
// from the top edge to the bottom edge decreased processor Y (rows) at constant
// processor X. Processor X therefore spans the 3200-pixel width left to right and
// processor Y the 2136-pixel height with the top edge high, so the panel reports
// X = processor X (range 0..column_maximum - 1) and Y = reversed processor Y
// (range 0..row_maximum - 1). That measurement did not vary with the panel module,
// so the reversal is unconditional.
//
// PanelVendor still reaches this function because the pinned upstream source passes
// it, but it must not change the result. On the unit measured here the kernel's
// panel_vendor reads "boe" (the lcd-id strap level) while the factory bootloader
// selects the CSOT display profile, so treating that value as the panel identity
// inverted the Y axis on hardware. Keep every call on the reversing branch.
// Corrections happen here only.
struct PanelAxes {
    int width;
    int height;
};

inline PanelAxes panelAxes(const Calibration &calibration) {
    return {calibration.column_maximum, calibration.row_maximum};
}

inline Contact panelContact(Contact contact, const Calibration &calibration, PanelVendor vendor) {
    const int x = std::clamp(contact.x, 0, calibration.column_maximum - 1);
    const int y = std::clamp(contact.y, 0, calibration.row_maximum - 1);
    contact.x = x;
    contact.y = vendor == PanelVendor::Boe ? y : calibration.row_maximum - 1 - y;
    return contact;
}

inline void panelSlots(std::span<const Slot> slots, const Calibration &calibration, PanelVendor vendor,
                       std::vector<Slot> &output) {
    output.assign(slots.begin(), slots.end());
    for (Slot &slot : output)
        slot.contact = panelContact(slot.contact, calibration, vendor);
}
} // namespace nvt

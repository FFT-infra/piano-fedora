// SPDX-License-Identifier: Apache-2.0
// Adapted from LiveTouchAdapter in xiaomi-sheng-thp; see the repository NOTICE.
#include "processor.hpp"
#include <algorithm>

namespace nvt {
namespace { constexpr size_t kStartupReferenceFrames = 72; }

void FingerProcessor::reset() {
    core_.reset();
    filter_.reset();
    reference_samples_.clear();
}

FingerProcessor::Update FingerProcessor::feed(const FingerFrame &frame, uint64_t timestamp_ns) {
    const auto &matrix = frame.matrix;
    const auto counter = frame.counter;
    const auto scan_mode = frame.scan_mode;
    if (scan_mode == 2 && !core_.hasReference()) {
        core_.process(matrix, counter, scan_mode);
        reference_samples_.clear();
        return {std::nullopt, true};
    }
    if (!core_.hasReference()) {
        reference_samples_.push_back(matrix);
        if (reference_samples_.size() < kStartupReferenceFrames)
            return {};
        Matrix reference{};
        std::array<int, kStartupReferenceFrames> values{};
        for (int node = 0; node < kNodes; ++node) {
            for (size_t sample = 0; sample < reference_samples_.size(); ++sample)
                values[sample] = reference_samples_[sample][node];
            std::sort(values.begin(), values.end());
            reference[node] = (values[kStartupReferenceFrames / 2 - 1] +
                               values[kStartupReferenceFrames / 2]) / 2;
        }
        reset();
        core_.process(reference, counter, 2);
        return {std::nullopt, true};
    }
    FrameResult result = core_.process(matrix, counter, scan_mode);
    std::array<const TrackedSlot *, kFingerSlots> tracked{};
    for (const TrackedSlot &slot : result.tracked_slots)
        tracked[slot.number] = &slot;
    std::vector<Slot> visible;
    for (int number = 0; number < kFingerSlots; ++number) {
        const TrackedSlot *slot = tracked[number];
        if (!slot) {
            filter_.processPipeline(number, 0, {}, {}, timestamp_ns);
            continue;
        }
        const FingerCoordinate coordinate{slot->contact.y, slot->contact.x};
        const auto filtered = filter_.processPipeline(
            number, slot->status, coordinate, coordinate, timestamp_ns, slot->age == 1);
        if (slot->age < 2)
            continue;
        Contact contact = slot->contact;
        contact.x = filtered.coordinate.y;
        contact.y = filtered.coordinate.x;
        visible.push_back(Slot{number, slot->tracking_id, std::move(contact)});
    }
    result.slots = std::move(visible);
    return {std::move(result), false};
}
} // namespace nvt

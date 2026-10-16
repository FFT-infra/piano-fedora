// SPDX-License-Identifier: Apache-2.0
// Adapted from LiveTouchAdapter in xiaomi-sheng-thp; see the repository NOTICE.
#pragma once

#include "frame.hpp"
#include "nvt_finger_filter.hpp"
#include <utility>

namespace nvt {
class FingerProcessor {
public:
    struct Update {
        std::optional<FrameResult> result;
        bool baseline_ready = false;
    };

    explicit FingerProcessor(Calibration calibration)
        : core_(calibration), filter_(calibration.row_maximum, calibration.column_maximum) {}
    void reset();
    Update feed(const FingerFrame &frame, uint64_t timestamp_ns);

private:
    TouchCore core_;
    FingerFilter filter_;
    std::vector<Matrix> reference_samples_;
};
} // namespace nvt

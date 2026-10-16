// SPDX-License-Identifier: MIT
#pragma once
#include "nvt_touch_core.hpp"
#include <filesystem>

namespace nvt {
Calibration loadCalibration(const std::filesystem::path &path);
} // namespace nvt

// SPDX-License-Identifier: Apache-2.0
// Fedora adapter: keep SunUEFI's single THP reader and pen owner, and pass
// finger records to yzddmr6/ianchb's pinned processing and lifecycle code.
#include "piano-finger-bridge.h"
#include "calibration.hpp"
#include "input.hpp"
#include "live_pipeline.hpp"
#include "orientation.hpp"
#include "panel.hpp"
#include <algorithm>
#include <cerrno>
#include <cstdio>
#include <filesystem>
#include <memory>
#include <stdexcept>

namespace {
// Mirrors NVT_THP_MISC_PREFIX in the kernel touch driver.
constexpr const char *nvtThpMiscPrefix = "nvt-thp-";

std::unique_ptr<nvt::UInputTouch> input;
std::unique_ptr<nvt::LivePipeline> pipeline;
nvt::Calibration calibration;
// Slots currently down in the uinput device. The core reports no finger slots
// while a palm is latched, so these are the contacts that have to be cancelled.
std::vector<nvt::Slot> lastFingers;
bool palmReported = false;

// The core's palm latch clears every contact. Reuse the last finger slots as
// MT_TOOL_PALM so libinput cancels the touch instead of treating it as a lift.
int minorAxis(const nvt::Contact &contact) {
    int minimum_row = nvt::kRows, maximum_row = -1;
    int minimum_column = nvt::kColumns, maximum_column = -1;
    for (int index = 0; index < nvt::kNodes; ++index) {
        if (!contact.nodes.test(index))
            continue;
        const int row = index / nvt::kColumns;
        const int column = index % nvt::kColumns;
        minimum_row = std::min(minimum_row, row);
        maximum_row = std::max(maximum_row, row);
        minimum_column = std::min(minimum_column, column);
        maximum_column = std::max(maximum_column, column);
    }
    if (maximum_row < minimum_row || maximum_column < minimum_column)
        return 0;
    return std::min(maximum_row - minimum_row,
                    maximum_column - minimum_column) + 1;
}

void reportFingers(std::vector<nvt::Slot> fingers) {
    palmReported = false;
    if (fingers.empty() && lastFingers.empty())
        return;
    input->report(fingers);
    lastFingers = std::move(fingers);
}

// Keep the contacts down with MT_TOOL_PALM. libinput turns that into a cancel,
// so a palm landing on the panel does not look like a finger lift or a tap.
void reportPalms() {
    if (palmReported || lastFingers.empty())
        return;
    for (auto &slot : lastFingers)
        slot.palm = true;
    input->report(lastFingers);
    palmReported = true;
}

template<class Function> int guarded(Function function) {
    try { function(); return 0; }
    catch (const std::exception &error) {
        std::fprintf(stderr, "piano finger pipeline: %s\n", error.what());
        return -EIO;
    }
}

// The reader opens /proc/nvt_thp_stream, which is not the misc node the
// attribute hangs off. The kernel names the misc node nvt-thp-<SPI device>, so
// the single nvt-thp-* entry under /sys/class/misc is this controller's.
std::filesystem::path thpMiscNode() {
    std::error_code error;
    for (const auto &entry : std::filesystem::directory_iterator("/sys/class/misc", error)) {
        const auto name = entry.path().filename().string();
        if (name.rfind(nvtThpMiscPrefix, 0) == 0)
            return entry.path();
    }
    return {};
}
}

extern "C" int piano_finger_open(const char *path) {
    return guarded([&] {
        if (!path || !*path)
            throw std::runtime_error("PIANO_FINGER_CALIBRATION must name the actual LCD calibration");
        calibration = nvt::loadCalibration(path);
        const auto axes = nvt::panelAxes(calibration);
        if (axes.width != 32000 || axes.height != 21360)
            throw std::runtime_error("unexpected Piano panel geometry");
        // The orientation comes from the one measurement taken on this panel with
        // a real finger: a top-to-bottom swipe decreases processor Y, so Y is
        // reversed. The kernel's panel_vendor does not decide it. That value is
        // the lcd-id strap level, and on the unit measured here it reads high
        // while the factory bootloader selected the CSOT panel profile, so the
        // two disagree; following panel_vendor inverted the Y axis on hardware.
        const auto identity = nvt::readPanelVendor(thpMiscNode());
        std::fprintf(stderr, "%s\n", nvt::describePanel(identity).c_str());
        input = std::make_unique<nvt::UInputTouch>(axes.width, axes.height);
        lastFingers.clear();
        palmReported = false;
        pipeline = std::make_unique<nvt::LivePipeline>(calibration,
            [](std::span<const nvt::Slot> slots) {
                if (slots.empty()) {
                    if (pipeline->palmActive())
                        reportPalms();
                    else
                        reportFingers({});
                    return;
                }
                std::vector<nvt::Slot> fingers;
                nvt::panelSlots(slots, calibration, nvt::PanelVendor::Csot, fingers);
                for (auto &slot : fingers)
                    slot.contact.minor = minorAxis(slot.contact);
                reportFingers(std::move(fingers));
            });
        std::fprintf(stderr, "piano finger: calibrated 32000x21360, Kalman/jitter/axis filtering, %s\n",
                     input->sysname().c_str());
    });
}

extern "C" int piano_finger_record(const uint8_t *payload, size_t size,
    uint64_t sequence, uint64_t timestamp, uint16_t checksum, uint16_t flags,
    uint32_t words, uint64_t now) {
    return guarded([&] {
        if (!pipeline) throw std::runtime_error("finger input not initialized");
        pipeline->feed({sequence, timestamp, checksum, flags, words, {payload, size}}, now);
    });
}

extern "C" int piano_finger_tick(uint64_t now) {
    return guarded([&] { if (pipeline) pipeline->expire(now); });
}
extern "C" int piano_finger_reset(void) {
    return guarded([] { if (pipeline) pipeline->reset(); });
}
extern "C" void piano_finger_close(void) {
    piano_finger_reset();
    pipeline.reset();
    input.reset();
}

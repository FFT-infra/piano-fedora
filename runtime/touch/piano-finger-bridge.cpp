// SPDX-License-Identifier: Apache-2.0
// Fedora adapter: keep SunUEFI's single THP reader and pen owner, and pass
// finger records to yzddmr6/ianchb's pinned processing and lifecycle code.
#include "piano-finger-bridge.h"
#include "calibration.hpp"
#include "input.hpp"
#include "live_pipeline.hpp"
#include "orientation.hpp"
#include "panel.hpp"
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
nvt::PanelVendor vendor = nvt::PanelVendor::Csot;
std::vector<nvt::Slot> panel;

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
        // The panel decides the processor-to-panel mapping: on BOE the Y axis
        // already runs top to bottom, so the CSOT reversal would flip it.
        const auto identity = nvt::readPanelVendor(thpMiscNode());
        vendor = identity.vendor;
        std::fprintf(stderr, "%s\n", nvt::describePanel(identity).c_str());
        input = std::make_unique<nvt::UInputTouch>(axes.width, axes.height);
        pipeline = std::make_unique<nvt::LivePipeline>(calibration,
            [](std::span<const nvt::Slot> slots) {
                nvt::panelSlots(slots, calibration, vendor, panel);
                input->report(panel);
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

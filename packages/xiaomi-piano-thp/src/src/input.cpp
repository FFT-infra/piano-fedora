// SPDX-License-Identifier: Apache-2.0
// Adapted from xiaomi-sheng-thp UInputTouch; see NOTICE.
#include "input.hpp"
#include <algorithm>
#include <cerrno>
#include <cstring>
#include <fcntl.h>
#include <linux/uinput.h>
#include <stdexcept>
#include <sys/ioctl.h>
#include <system_error>
#include <unistd.h>

namespace nvt {
namespace {
void setupAxis(int fd, unsigned code, int maximum) {
    uinput_abs_setup setup{};
    setup.code = code;
    setup.absinfo.maximum = maximum;
    if (code == ABS_MT_POSITION_X || code == ABS_MT_POSITION_Y) {
        const int millimetres = code == ABS_MT_POSITION_X ? 239 : 163;
        setup.absinfo.resolution = (maximum + 1 + millimetres / 2) / millimetres;
        // One panel pixel in the calibrated ten-units-per-pixel coordinate system.
        setup.absinfo.fuzz = kSuperResolution;
    }
    if (ioctl(fd, UI_ABS_SETUP, &setup) < 0)
        throw std::system_error(errno, std::generic_category(), "UI_ABS_SETUP");
}

void writeEvents(int fd, std::span<const input_event> events) {
    while (!events.empty()) {
        const ssize_t written = write(fd, events.data(), events.size_bytes());
        if (written < 0 && errno == EINTR)
            continue;
        if (written < 0)
            throw std::system_error(errno, std::generic_category(), "write uinput");
        if (!written || written % sizeof(input_event))
            throw std::runtime_error("short non-event-aligned uinput write");
        events = events.subspan(written / sizeof(input_event));
    }
}
} // namespace

UInputTouch::UInputTouch(int width, int height)
    : maximum_x_(width > 0 ? width - 1 : 0), maximum_y_(height > 0 ? height - 1 : 0) {
    if (width <= 0 || height <= 0)
        throw std::invalid_argument("invalid touch dimensions");
    active_.fill(-1);
    ids_.fill(-1);
    events_.reserve(kFingerSlots * 8 + 3);
    fd_ = open("/dev/uinput", O_WRONLY | O_CLOEXEC);
    if (fd_ < 0)
        throw std::system_error(errno, std::generic_category(), "open /dev/uinput");
    try {
        for (unsigned type : {EV_KEY, EV_ABS})
            checkedIoctl(UI_SET_EVBIT, type);
        checkedIoctl(UI_SET_KEYBIT, BTN_TOUCH);
        checkedIoctl(UI_SET_KEYBIT, BTN_TOOL_FINGER);
        for (unsigned axis : {ABS_MT_SLOT, ABS_MT_TOUCH_MAJOR, ABS_MT_POSITION_X,
                              ABS_MT_POSITION_Y, ABS_MT_TOOL_TYPE, ABS_MT_TRACKING_ID,
                              ABS_MT_PRESSURE})
            checkedIoctl(UI_SET_ABSBIT, axis);
        checkedIoctl(UI_SET_PROPBIT, INPUT_PROP_DIRECT);
        uinput_setup setup{};
        setup.id.bustype = BUS_VIRTUAL;
        setup.id.vendor = 0x2717;
        setup.id.product = 0x3653;
        setup.id.version = 1;
        std::strncpy(setup.name, "Piano THP Touchscreen", sizeof(setup.name) - 1);
        if (ioctl(fd_, UI_DEV_SETUP, &setup) < 0)
            throw std::system_error(errno, std::generic_category(), "UI_DEV_SETUP");
        setupAxis(fd_, ABS_MT_SLOT, kFingerSlots - 1);
        setupAxis(fd_, ABS_MT_TOUCH_MAJOR, 255);
        setupAxis(fd_, ABS_MT_POSITION_X, maximum_x_);
        setupAxis(fd_, ABS_MT_POSITION_Y, maximum_y_);
        setupAxis(fd_, ABS_MT_TOOL_TYPE, MT_TOOL_PALM);
        setupAxis(fd_, ABS_MT_TRACKING_ID, 65535);
        setupAxis(fd_, ABS_MT_PRESSURE, 1);
        if (ioctl(fd_, UI_DEV_CREATE) < 0)
            throw std::system_error(errno, std::generic_category(), "UI_DEV_CREATE");
    } catch (...) {
        close(fd_);
        throw;
    }
}

UInputTouch::~UInputTouch() {
    if (fd_ >= 0) {
        try { report({}); } catch (...) {}
        ioctl(fd_, UI_DEV_DESTROY);
        close(fd_);
    }
}

void UInputTouch::checkedIoctl(unsigned long request, unsigned long value) {
    if (ioctl(fd_, request, value) < 0)
        throw std::system_error(errno, std::generic_category(), "uinput capability setup");
}

std::string UInputTouch::sysname() const {
    std::array<char, 128> name{};
    if (ioctl(fd_, UI_GET_SYSNAME(name.size()), name.data()) < 0)
        throw std::system_error(errno, std::generic_category(), "UI_GET_SYSNAME");
    return name.data();
}

int UInputTouch::allocateTrackingId() {
    while (std::find(ids_.begin(), ids_.end(), next_id_) != ids_.end())
        next_id_ = (next_id_ + 1) & 0xffff;
    const int id = next_id_;
    next_id_ = (next_id_ + 1) & 0xffff;
    return id;
}

void UInputTouch::report(std::span<const Slot> slots) {
    std::array<const Slot *, kFingerSlots> visible{};
    for (const auto &slot : slots) {
        if (slot.number < 0 || slot.number >= kFingerSlots || slot.tracking_id < 0 || visible[slot.number])
            throw std::invalid_argument("invalid or duplicate touch slot");
        visible[slot.number] = &slot;
    }
    events_.clear();
    auto event = [this](uint16_t type, uint16_t code, int value) {
        input_event input{};
        input.type = type;
        input.code = code;
        input.value = value;
        events_.push_back(input);
    };
    for (int number = 0; number < kFingerSlots; ++number) {
        if (active_[number] >= 0 && !visible[number]) {
            event(EV_ABS, ABS_MT_SLOT, number);
            event(EV_ABS, ABS_MT_TRACKING_ID, -1);
            active_[number] = -1;
            ids_[number] = -1;
        }
    }
    for (int number = 0; number < kFingerSlots; ++number) {
        const auto *slot = visible[number];
        if (!slot)
            continue;
        event(EV_ABS, ABS_MT_SLOT, number);
        if (active_[number] != slot->tracking_id) {
            ids_[number] = allocateTrackingId();
            active_[number] = slot->tracking_id;
            event(EV_ABS, ABS_MT_TRACKING_ID, ids_[number]);
        }
        event(EV_ABS, ABS_MT_POSITION_X, std::clamp(slot->contact.x, 0, maximum_x_));
        event(EV_ABS, ABS_MT_POSITION_Y, std::clamp(slot->contact.y, 0, maximum_y_));
        event(EV_ABS, ABS_MT_TOOL_TYPE, MT_TOOL_FINGER);
        event(EV_ABS, ABS_MT_TOUCH_MAJOR, std::clamp(slot->contact.area, 0, 255));
        event(EV_ABS, ABS_MT_PRESSURE, 1);
    }
    event(EV_KEY, BTN_TOUCH, !slots.empty());
    event(EV_KEY, BTN_TOOL_FINGER, !slots.empty());
    event(EV_SYN, SYN_REPORT, 0);
    writeEvents(fd_, events_);
}
} // namespace nvt

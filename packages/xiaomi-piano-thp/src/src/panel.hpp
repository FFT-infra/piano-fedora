// SPDX-License-Identifier: MIT
#pragma once
#include <filesystem>
#include <optional>
#include <string>

namespace nvt {
// Display/touch module fitted to the unit. The kernel touch driver reads the
// panel ID pin and is the only authority; this program never guesses it.
enum class PanelVendor { Csot, Boe };

struct PanelIdentity {
    PanelVendor vendor = PanelVendor::Csot;
    // False when the kernel does not export panel_vendor (older kernels):
    // the panel is then treated as CSOT, the only panel those kernels supported.
    bool reported = false;
    // A reported value other than "csot" or "boe"; CSOT is used so touch keeps working.
    std::string unknown;
};

// Reads SYSFS/class/misc/NAME/device/panel_vendor, where NAME is the name of the
// THP misc node after resolving links (/dev/piano-touch -> /dev/nvt-thp-spi0.0).
// A missing attribute or an unknown value means CSOT.
PanelIdentity readPanelVendor(const std::filesystem::path &node,
                              const std::filesystem::path &sysfs = "/sys");

const char *panelVendorName(PanelVendor vendor);

// The one log line for the identity; it starts with "THP panel error" for unknown values.
std::string describePanel(const PanelIdentity &identity);

// Describes a calibration whose stock project_vendor_id (CSOT 4, BOE 26 in the
// stock piano_nova_*_thp_config.ini) does not belong to the fitted panel. Empty
// when it matches or when the calibration does not record the ID.
std::optional<std::string> calibrationMismatch(PanelVendor vendor,
                                               std::optional<int> project_vendor_id);
} // namespace nvt

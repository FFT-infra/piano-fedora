#!/usr/bin/env bash
# ==============================================================================
# flash-linux.sh - Xiaomi Pad 8 Pro (piano) Fedora 44 Linux Flashing Tool
# ==============================================================================
set -euo pipefail

# ANSI color styling
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m' # No Color

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK_DIR="$SCRIPT_DIR"

# Defaults
BOOT_IMG=""
ESP_IMG=""
ROOT_IMG=""
AUTO_CONFIRM=0
DO_REBOOT=1
SKIP_CHECKSUM=0

usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Flashing utility for Fedora 44 Linux on Xiaomi Pad 8 Pro (piano).

Options:
  -y, --yes            Skip safety confirmation prompt (non-interactive)
  --boot <file>        Path to SunUEFI boot image (boot_sunuefi.img)
  --esp <file>         Path to SunUEFI ESP image (sunuefi_esp.img)
  --root <file>        Path to Fedora rootfs image (pianoroot-fedora44-gnome-f2fs.img)
  --no-reboot          Do not reboot the device after flashing completes
  --skip-checksum      Skip verifying SHA256SUMS before flashing
  -h, --help           Show this help message and exit

EOF
    exit 0
}

# Parse command line options
while [[ $# -gt 0 ]]; do
    case "$1" in
        -y|--yes)
            AUTO_CONFIRM=1
            shift
            ;;
        --boot)
            BOOT_IMG="$2"
            shift 2
            ;;
        --esp)
            ESP_IMG="$2"
            shift 2
            ;;
        --root)
            ROOT_IMG="$2"
            shift 2
            ;;
        --no-reboot)
            DO_REBOOT=0
            shift
            ;;
        --skip-checksum)
            SKIP_CHECKSUM=1
            shift
            ;;
        -h|--help)
            usage
            ;;
        *)
            echo -e "${RED}Error: Unknown argument '$1'${NC}" >&2
            exit 1
            ;;
    esac
done

echo -e "${BLUE}${BOLD}"
echo "===================================================================="
echo "    Xiaomi Pad 8 Pro (piano) - Fedora 44 Linux Flashing Tool        "
echo "===================================================================="
echo -e "${NC}"

# 1. Prerequisite check: fastboot CLI
if ! command -v fastboot &>/dev/null; then
    echo -e "${RED}[ERROR] 'fastboot' command not found!${NC}"
    echo "Please install Android Platform Tools (adb & fastboot) and ensure it is in PATH."
    echo "  - Fedora: sudo dnf install android-tools"
    echo "  - Ubuntu/Debian: sudo apt install android-tools-fastboot"
    echo "  - macOS: brew install android-platform-tools"
    exit 1
fi

FASTBOOT_VER=$(fastboot --version 2>&1 | head -n 1)
echo -e "[INFO] Found Fastboot: ${GREEN}${FASTBOOT_VER}${NC}"

# 2. Device connection check
echo -e "[INFO] Checking connected Fastboot devices..."
DEVICE_COUNT=$(fastboot devices | grep -v '^$' | wc -l)
if [ "$DEVICE_COUNT" -eq 0 ]; then
    echo -e "${RED}[ERROR] No device detected in Fastboot mode!${NC}"
    echo "Please ensure:"
    echo "  1. Tablet is powered off completely."
    echo "  2. Hold [Power] + [Volume Down] until the FASTBOOT bunny/screen appears."
    echo "  3. Connect USB cable firmly to PC."
    echo "  4. (Linux only) Check udev rules if permissions fail: sudo fastboot devices"
    exit 1
fi

FASTBOOT_SERIAL=$(fastboot devices | head -n 1 | awk '{print $1}')
echo -e "[INFO] Detected Fastboot device: ${GREEN}${FASTBOOT_SERIAL}${NC}"

# 3. Product model validation
echo -e "[INFO] Querying device product model..."
PRODUCT_ID=$(fastboot getvar product 2>&1 | grep '^product:' | awk '{print $2}' | tr -d '\r\n')
if [ -n "$PRODUCT_ID" ]; then
    if [ "$PRODUCT_ID" != "piano" ]; then
        echo -e "${YELLOW}[WARNING] Device product reported as '$PRODUCT_ID' (expected 'piano')!${NC}"
        echo "Flashing onto a non-piano device can cause permanent bricking."
    else
        echo -e "[INFO] Product check passed: ${GREEN}${PRODUCT_ID} (Xiaomi Pad 8 Pro)${NC}"
    fi
else
    echo -e "${YELLOW}[WARNING] Could not read 'product' variable from bootloader.${NC}"
fi

# 4. Image file resolution
find_file() {
    local candidate
    for candidate in "$@"; do
        if [ -f "$WORK_DIR/$candidate" ]; then
            echo "$WORK_DIR/$candidate"
            return 0
        fi
        if [ -f "$candidate" ]; then
            echo "$candidate"
            return 0
        fi
    done
    return 1
}

# Auto-detect boot image
if [ -z "$BOOT_IMG" ]; then
    if FOUND=$(find_file "boot_sunuefi.img" "boot_linux.img" "boot.img"); then
        BOOT_IMG="$FOUND"
    else
        echo -e "${RED}[ERROR] Missing boot image (boot_sunuefi.img)!${NC}"
        exit 1
    fi
fi

# Auto-detect ESP image
if [ -z "$ESP_IMG" ]; then
    if FOUND=$(find_file "sunuefi_esp.img" "esp.img"); then
        ESP_IMG="$FOUND"
    else
        echo -e "${RED}[ERROR] Missing ESP image (sunuefi_esp.img)!${NC}"
        exit 1
    fi
fi

# Auto-detect RootFS image
# ROOT_HASH_FILE is what SHA256SUMS names; ROOT_UNPACKED is true when it is a
# .zst that still has to be expanded. Verification runs before decompression,
# so the checksum is always computed on the published artifact.
ROOT_HASH_FILE=""
ROOT_UNPACKED=0
if [ -z "$ROOT_IMG" ]; then
    if FOUND=$(find_file "pianoroot-fedora44-gnome-f2fs.img" "pianoroot.f2fs.sparse.img" "pianoroot.f2fs.img"); then
        ROOT_IMG="$FOUND"
        ROOT_HASH_FILE="$FOUND"
    elif FOUND_ZST=$(find_file "pianoroot-fedora44-gnome-f2fs.img.zst" "pianoroot.f2fs.sparse.img.zst"); then
        ROOT_HASH_FILE="$FOUND_ZST"
        ROOT_UNPACKED=1
    else
        echo -e "${RED}[ERROR] Missing RootFS image (pianoroot-fedora44-gnome-f2fs.img)!${NC}"
        exit 1
    fi
else
    ROOT_HASH_FILE="$ROOT_IMG"
    case "$ROOT_IMG" in
        *.zst) ROOT_UNPACKED=1 ;;
    esac
fi

# 5. Integrity verification (SHA256SUMS)
# Manifest entries are written by package-release-bundle.sh as "./name", so a
# bare "$name$" match would never hit and the whole check would silently pass.
manifest_hash() {
    awk -v want="$1" '
        {
            name = $2
            sub(/^\*/, "", name)
            sub(/^\.\//, "", name)
            if (name == want) { print $1; exit }
        }
    ' "$WORK_DIR/SHA256SUMS"
}

if [ "$SKIP_CHECKSUM" -eq 0 ]; then
    if [ ! -f "$WORK_DIR/SHA256SUMS" ]; then
        echo -e "${RED}[ERROR] Missing SHA256SUMS next to the images.${NC}"
        echo "Refusing to flash unverified images. Re-download the Release bundle,"
        echo "or pass --skip-checksum only if you verified the images yourself."
        exit 1
    fi
    echo -e "[INFO] Verifying image integrity with SHA256SUMS..."
    CHKSUM_TOOL=""
    if command -v sha256sum &>/dev/null; then
        CHKSUM_TOOL="sha256sum"
    elif command -v shasum &>/dev/null; then
        CHKSUM_TOOL="shasum -a 256"
    else
        echo -e "${RED}[ERROR] Neither sha256sum nor shasum is available.${NC}"
        echo "Install coreutils (Linux) or use --skip-checksum after verifying manually."
        exit 1
    fi

    for target_file in "$BOOT_IMG" "$ESP_IMG" "$ROOT_HASH_FILE"; do
        target_name=$(basename "$target_file")
        expected_hash=$(manifest_hash "$target_name")
        if [ -z "$expected_hash" ]; then
            # A manually extracted .img is not listed; the manifest names the
            # .zst it came from, so fall back to verifying that archive.
            archive="${target_file}.zst"
            archive_name="${target_name}.zst"
            if [ -f "$archive" ] && [ -n "$(manifest_hash "$archive_name")" ]; then
                target_file="$archive"
                target_name="$archive_name"
                expected_hash=$(manifest_hash "$target_name")
            fi
        fi
        if [ -z "$expected_hash" ]; then
            echo -e "${RED}[ERROR] ${target_name} is not listed in SHA256SUMS.${NC}"
            echo "The image set does not match the published manifest. Re-download."
            exit 1
        fi
        actual_hash=$($CHKSUM_TOOL "$target_file" | awk '{print $1}')
        if [ "$actual_hash" != "$expected_hash" ]; then
            echo -e "${RED}[ERROR] SHA256 mismatch for ${target_name}!${NC}"
            echo "  Expected: $expected_hash"
            echo "  Actual  : $actual_hash"
            echo "The image file appears corrupted. Please re-download."
            exit 1
        fi
        echo -e "  ${GREEN}✓${NC} ${target_name} checksum valid."
    done
fi

# 6. Expand a compressed rootfs only after its published digest is confirmed.
if [ "$ROOT_UNPACKED" -eq 1 ]; then
    TARGET_UNPACK="${ROOT_HASH_FILE%.zst}"
    if ! command -v zstd &>/dev/null; then
        echo -e "${RED}[ERROR] 'zstd' command not found to decompress rootfs archive!${NC}"
        echo "Please install zstd (sudo dnf install zstd / brew install zstd) or extract manually."
        exit 1
    fi
    echo -e "[INFO] Decompressing rootfs with zstd: $TARGET_UNPACK ..."
    zstd -d -f "$ROOT_HASH_FILE" -o "$TARGET_UNPACK"
    ROOT_IMG="$TARGET_UNPACK"
fi

echo -e "[INFO] Flashing targets resolved:"
echo -e "  - Boot image   : ${GREEN}${BOOT_IMG}${NC} ($(stat -c%s "$BOOT_IMG" 2>/dev/null || stat -f%z "$BOOT_IMG") bytes)"
echo -e "  - ESP image    : ${GREEN}${ESP_IMG}${NC} ($(stat -c%s "$ESP_IMG" 2>/dev/null || stat -f%z "$ESP_IMG") bytes)"
echo -e "  - RootFS image : ${GREEN}${ROOT_IMG}${NC} ($(stat -c%s "$ROOT_IMG" 2>/dev/null || stat -f%z "$ROOT_IMG") bytes)"

# 7. Safety Confirmation
if [ "$AUTO_CONFIRM" -eq 0 ]; then
    echo -e "\n${YELLOW}${BOLD}⚠️  CRITICAL SAFETY WARNING ⚠️${NC}"
    echo "1. This action will write Linux SunUEFI to Slot B (boot_b, dtbo_b)."
    echo "2. This action will overwrite 'sunuefi_esp' and 'sunuefi_root' partitions."
    echo "3. Existing Android on Slot A is preserved for dual-boot recovery."
    echo "4. Ensure your Bootloader is UNLOCKED and battery is above 30%."
    echo ""
    read -rp "Are you sure you want to proceed? Type 'YES' in all caps to continue: " CONFIRM_INPUT
    if [ "$CONFIRM_INPUT" != "YES" ]; then
        echo -e "${YELLOW}Aborted by user. No modifications were made to the device.${NC}"
        exit 0
    fi
fi

# 7. Flashing execution sequence
echo -e "\n${BLUE}${BOLD}=== Starting Flashing Sequence ===${NC}"

echo -e "\n--> Step 1/5: Setting active boot slot to B..."
fastboot set_active b

echo -e "\n--> Step 2/5: Erasing dtbo_b (SunUEFI uses unified internal DTB)..."
fastboot erase dtbo_b

echo -e "\n--> Step 3/5: Flashing SunUEFI bootloader header to boot_b..."
fastboot flash boot_b "$BOOT_IMG"

echo -e "\n--> Step 4/5: Flashing SunUEFI ESP to sunuefi_esp..."
fastboot flash sunuefi_esp "$ESP_IMG"

echo -e "\n--> Step 5/5: Flashing Fedora 44 RootFS to sunuefi_root..."
fastboot flash sunuefi_root "$ROOT_IMG"

echo -e "\n${GREEN}${BOLD}====================================================================${NC}"
echo -e "${GREEN}${BOLD}             🎉 Flashing Completed Successfully! 🎉                 ${NC}"
echo -e "${GREEN}${BOLD}====================================================================${NC}"
echo ""
echo "System Summary:"
echo "  - Default desktop user : piano"
echo "  - Default password     : piano123456"
echo "  - SunUEFI boot menu    : Press Volume keys during initial 3-second logo"
echo "  - Switch back to Android: Open GNOME App Drawer -> Click 'Return to Android'"
echo ""

if [ "$DO_REBOOT" -eq 1 ]; then
    echo -e "--> Rebooting tablet into Fedora 44 Linux..."
    fastboot reboot
    echo -e "${GREEN}Device reboot triggered. Enjoy Fedora Linux on Xiaomi Pad 8 Pro!${NC}"
else
    echo -e "Device left in Fastboot mode (--no-reboot specified)."
fi

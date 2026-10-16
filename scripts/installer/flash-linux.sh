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
if [ -z "$ROOT_IMG" ]; then
    if FOUND=$(find_file "pianoroot-fedora44-gnome-f2fs.img" "pianoroot.f2fs.sparse.img" "pianoroot.f2fs.img"); then
        ROOT_IMG="$FOUND"
    elif FOUND_ZST=$(find_file "pianoroot-fedora44-gnome-f2fs.img.zst" "pianoroot.f2fs.sparse.img.zst"); then
        echo -e "[INFO] Found compressed rootfs archive: ${YELLOW}${FOUND_ZST}${NC}"
        TARGET_UNPACK="${FOUND_ZST%.zst}"
        if command -v zstd &>/dev/null; then
            echo -e "[INFO] Decompressing rootfs with zstd: $TARGET_UNPACK ..."
            zstd -d -k --rm "$FOUND_ZST" -o "$TARGET_UNPACK"
            ROOT_IMG="$TARGET_UNPACK"
        else
            echo -e "${RED}[ERROR] 'zstd' command not found to decompress rootfs archive!${NC}"
            echo "Please install zstd (sudo dnf install zstd / brew install zstd) or extract manually."
            exit 1
        fi
    else
        echo -e "${RED}[ERROR] Missing RootFS image (pianoroot-fedora44-gnome-f2fs.img)!${NC}"
        exit 1
    fi
fi

echo -e "[INFO] Flashing targets resolved:"
echo -e "  - Boot image   : ${GREEN}${BOOT_IMG}${NC} ($(stat -c%s "$BOOT_IMG" 2>/dev/null || stat -f%z "$BOOT_IMG") bytes)"
echo -e "  - ESP image    : ${GREEN}${ESP_IMG}${NC} ($(stat -c%s "$ESP_IMG" 2>/dev/null || stat -f%z "$ESP_IMG") bytes)"
echo -e "  - RootFS image : ${GREEN}${ROOT_IMG}${NC} ($(stat -c%s "$ROOT_IMG" 2>/dev/null || stat -f%z "$ROOT_IMG") bytes)"

# 5. Integrity verification (SHA256SUMS)
if [ "$SKIP_CHECKSUM" -eq 0 ] && [ -f "$WORK_DIR/SHA256SUMS" ]; then
    echo -e "[INFO] Verifying image integrity with SHA256SUMS..."
    CHKSUM_TOOL=""
    if command -v sha256sum &>/dev/null; then
        CHKSUM_TOOL="sha256sum"
    elif command -v shasum &>/dev/null; then
        CHKSUM_TOOL="shasum -a 256"
    fi

    if [ -n "$CHKSUM_TOOL" ]; then
        for target_file in "$BOOT_IMG" "$ESP_IMG" "$ROOT_IMG"; do
            target_name=$(basename "$target_file")
            expected_hash=$(grep -E "[[:space:]]\*?${target_name}$" "$WORK_DIR/SHA256SUMS" | awk '{print $1}' || true)
            if [ -n "$expected_hash" ]; then
                actual_hash=$($CHKSUM_TOOL "$target_file" | awk '{print $1}')
                if [ "$actual_hash" != "$expected_hash" ]; then
                    echo -e "${RED}[ERROR] SHA256 mismatch for ${target_name}!${NC}"
                    echo "  Expected: $expected_hash"
                    echo "  Actual  : $actual_hash"
                    echo "The image file appears corrupted. Please re-download."
                    exit 1
                else
                    echo -e "  ${GREEN}✓${NC} ${target_name} checksum valid."
                fi
            fi
        done
    fi
fi

# 6. Safety Confirmation
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

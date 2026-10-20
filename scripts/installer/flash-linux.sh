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
# The installer ZIP is extracted into the same staging directory as the
# deploy-bundle assets. Use the caller's directory for assets, while still
# accepting an invocation by path from elsewhere.
WORK_DIR="$(pwd)"

# Defaults
BOOT_IMG=""
ESP_IMG=""
ROOT_IMG=""
ROOT_SHA256SUMS=""
SSH_KEY=""
AUTO_CONFIRM=0
DO_REBOOT=1
SKIP_CHECKSUM=0

usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Flashing utility for Fedora 44 Linux on Xiaomi Pad 8 Pro (piano).

Options:
  -y, --yes            Skip safety confirmation prompt (non-interactive)
  --boot <file>        Device-specific boot_b image (boot_linux.img from
                       prepare-device-boot.py). Never the bundle's boot_sunuefi.img.
  --esp <file>         Path to SunUEFI ESP image (sunuefi_esp.img)
  --root <file>        Path to the prepared rootfs transport (pianoroot.f2fs.sparse.img)
  --root-sha256sums <file>
                       root-install/SHA256SUMS produced by prepare-install
  --ssh-key <file>     One ssh-ed25519 or ssh-rsa public key for user piano.
                       The key is copied into the ESP image after every
                       checksum passes; the device imports it on first boot and
                       then deletes the ESP copy. Without this option the
                       system keeps the documented password login only.
  --no-reboot          Do not reboot the device after flashing completes
  --skip-checksum      Legacy option; refused because first install requires both manifests
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
        --root-sha256sums)
            ROOT_SHA256SUMS="$2"
            shift 2
            ;;
        --ssh-key)
            SSH_KEY="$2"
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
# `fastboot devices` exits non-zero when it finds nothing, and `grep -v '^$'`
# exits 1 on empty input. Under `set -euo pipefail` either would abort the script
# right here, before the error message below ever printed. `|| true` keeps the
# failure path inside our own check.
DEVICE_LIST=$(fastboot devices 2>/dev/null | grep -v '^$' || true)
DEVICE_COUNT=$(printf '%s\n' "$DEVICE_LIST" | grep -c . || true)
if [ "$DEVICE_COUNT" -eq 0 ]; then
    echo -e "${RED}[ERROR] No device detected in Fastboot mode!${NC}"
    echo "Please ensure:"
    echo "  1. Tablet is powered off completely."
    echo "  2. Hold [Power] + [Volume Down] until the FASTBOOT bunny/screen appears."
    echo "  3. Connect USB cable firmly to PC."
    echo "  4. (Linux only) Check udev rules if permissions fail: sudo fastboot devices"
    exit 1
fi

if [ "$DEVICE_COUNT" -gt 1 ]; then
    echo -e "${RED}[ERROR] ${DEVICE_COUNT} Fastboot devices detected.${NC}"
    echo "Disconnect every other device; flashing the wrong one is unrecoverable."
    printf '%s\n' "$DEVICE_LIST" | sed 's/^/  /'
    exit 1
fi

FASTBOOT_SERIAL=$(printf '%s\n' "$DEVICE_LIST" | head -n 1 | awk '{print $1}')
echo -e "[INFO] Detected Fastboot device: ${GREEN}${FASTBOOT_SERIAL}${NC}"

# Every fastboot call below is pinned to this serial so a second tablet that
# shows up mid-flash can never receive a partition write.
fb() {
    fastboot -s "$FASTBOOT_SERIAL" "$@"
}

# 3. Product model validation
echo -e "[INFO] Querying device product model..."
PRODUCT_ID=$(fb getvar product 2>&1 | grep '^product:' | awk '{print $2}' | tr -d '\r\n' || true)
if [ -n "$PRODUCT_ID" ]; then
    if [ "$PRODUCT_ID" != "piano" ]; then
        echo -e "${RED}[ERROR] Device product reported as '$PRODUCT_ID', expected 'piano'.${NC}"
        echo "Refusing to flash a different device model; that can cause a hard brick."
        exit 1
    else
        echo -e "[INFO] Product check passed: ${GREEN}${PRODUCT_ID} (Xiaomi Pad 8 Pro)${NC}"
    fi
else
    echo -e "${RED}[ERROR] Could not read 'product' from the bootloader.${NC}"
    echo "Without it the device model cannot be confirmed. Check the cable and retry."
    exit 1
fi

# 3b. Bootloader state: must be stock ABL, not fastbootd, and unlocked.
UNLOCKED=$(fb getvar unlocked 2>&1 | grep '^unlocked:' | awk '{print $2}' | tr -d '\r\n' || true)
if [ "$UNLOCKED" != "yes" ]; then
    echo -e "${RED}[ERROR] Bootloader is not unlocked (unlocked='${UNLOCKED}').${NC}"
    echo "Unlock it through Xiaomi's official process before flashing."
    exit 1
fi

USERSPACE=$(fb getvar is-userspace 2>&1 | grep '^is-userspace:' | awk '{print $2}' | tr -d '\r\n' || true)
if [ "$USERSPACE" != "no" ]; then
    echo -e "${RED}[ERROR] Device is in fastbootd (is-userspace='${USERSPACE}').${NC}"
    echo "Reboot to the stock bootloader: fastboot reboot bootloader"
    exit 1
fi

CURRENT_SLOT=$(fb getvar current-slot 2>&1 | grep '^current-slot:' | awk '{print $2}' | tr -d '\r\n' || true)
if [ "$CURRENT_SLOT" != "a" ] && [ "$CURRENT_SLOT" != "b" ]; then
    echo -e "${RED}[ERROR] Could not read a valid current-slot ('${CURRENT_SLOT}').${NC}"
    exit 1
fi
if [ "$CURRENT_SLOT" != "a" ]; then
    echo -e "${RED}[ERROR] Android must be active on Slot A before running this installer.${NC}"
    echo "This installer always writes Linux to Slot B (boot_b); continuing from"
    echo "Android Slot B would overwrite the only Android boot path."
    exit 1
fi

# 3c. The target partitions must exist and be big enough. Flashing a 512 MiB ESP
# into a missing or shrunken partition is exactly the kind of silent corruption
# that leaves the tablet unbootable, so refuse before any write happens.
# `fastboot getvar partition-size:*` prints hexadecimal, including digit-only
# values. Parse it as hex before comparing with decimal manifest bytes.
fastboot_bytes_hex() {
    local value="$1"
    case "$value" in
        0[xX]*) value="${value#0[xX]}" ;;
    esac
    [[ "$value" =~ ^[0-9a-fA-F]+$ ]] || return 1
    printf '%d\n' "$((16#$value))"
}

require_partition() {
    local name="$1" need="$2" size
    size=$(fb getvar "partition-size:${name}" 2>&1 | grep "partition-size:${name}:" \
           | awk '{print $2}' | tr -d '\r\n' || true)
    if [ -z "$size" ]; then
        echo -e "${RED}[ERROR] Partition '${name}' not found on the device.${NC}"
        echo "This tablet has not been partitioned for Linux yet; run the first-partition flow."
        exit 1
    fi
    raw_size="$size"
    if ! size=$(fastboot_bytes_hex "$size"); then
        echo -e "${RED}[ERROR] Partition '${name}' has an invalid size: ${raw_size}.${NC}"
        exit 1
    fi
    if [ "$((size))" -lt "$need" ]; then
        echo -e "${RED}[ERROR] Partition '${name}' is too small (${size} < ${need} bytes).${NC}"
        exit 1
    fi
    if [ -n "${3:-}" ]; then
        printf -v "$3" '%s' "$size"
    fi
    echo -e "  ${GREEN}✓${NC} ${name} present, $(printf '%s' "$size") bytes"
}

echo -e "[INFO] Verifying target partitions..."
ROOT_PARTITION_SIZE=""
require_partition boot_b 67108864
require_partition sunuefi_esp 536870912
require_partition sunuefi_root 1073741824 ROOT_PARTITION_SIZE

# 4. Image file resolution
find_file() {
    local candidate
    for candidate in "$@"; do
        if [ -f "$WORK_DIR/$candidate" ]; then
            work_real=$(cd "$WORK_DIR" && pwd -P)
            printf '%s/%s\n' "$work_real" "${candidate#./}"
            return 0
        fi
        if [ -f "$candidate" ]; then
            candidate_dir=$(cd "$(dirname "$candidate")" && pwd -P)
            printf '%s/%s\n' "$candidate_dir" "$(basename "$candidate")"
            return 0
        fi
    done
    return 1
}

# Auto-detect the boot_b image.
# deploy-bundle/boot_sunuefi.img is the Linux payload that lives inside the
# ESP (EFI/Piano/stable/boot.img): a Header v2 container with no AVB footer and
# no SUNUEFI-NEXTv1 page. SunUEFI's UEFI firmware loads it; ABL cannot. Writing
# it to boot_b leaves the tablet in Fastboot with no bootable slot, so it is
# never used here. boot_b takes the device-specific image that
# prepare-device-boot.py repacks from the stock 96 MiB BOOT.
if [ -z "$BOOT_IMG" ]; then
    if FOUND=$(find_file "boot_linux.img"); then
        BOOT_IMG="$FOUND"
    elif FOUND=$(find_file "device-boot/boot_linux.img"); then
        BOOT_IMG="$FOUND"
    fi
elif [ "$(basename "$BOOT_IMG")" = "boot_sunuefi.img" ]; then
    echo -e "${RED}[ERROR] --boot is the bundle's boot_sunuefi.img, which is the ESP payload, not a boot_b image.${NC}"
    echo "Pass device-boot/boot_linux.img from prepare-device-boot.py instead."
    exit 1
fi

# Auto-detect ESP image
if [ -z "$ESP_IMG" ]; then
    if FOUND=$(find_file "sunuefi_esp.img"); then
        ESP_IMG="$FOUND"
    else
        echo -e "${RED}[ERROR] Missing ESP image (sunuefi_esp.img)!${NC}"
        exit 1
    fi
fi

# Auto-detect RootFS image.
# The deploy-bundle ships the sealed 8 GiB root. First install cannot flash it
# directly: prepare-install expands it to the actual sunuefi_root capacity and
# writes the device configuration. Only root-install/ is a supported layout;
# accepting a top-level sparse file could silently select an older generation.
if [ -z "$ROOT_IMG" ]; then
    if FOUND=$(find_file "root-install/pianoroot.f2fs.sparse.img"); then
        ROOT_IMG="$FOUND"
    else
        echo -e "${RED}[ERROR] Missing prepared rootfs transport: pianoroot.f2fs.sparse.img${NC}"
        echo "Run prepare-install for this deploy-bundle first. A sealed"
        echo "pianoroot.f2fs.img.zst is not a first-install image."
        exit 1
    fi
else
    case "$ROOT_IMG" in
        *.sparse.img) ;;
        *)
            echo -e "${RED}[ERROR] --root must name pianoroot.f2fs.sparse.img from prepare-install.${NC}"
            echo "Raw or compressed deploy-bundle roots are not resized for this tablet."
            exit 1
            ;;
    esac
fi
ROOT_DIR=$(cd "$(dirname "$ROOT_IMG")" && pwd -P)
if [ "$ROOT_DIR" != "$(cd "$WORK_DIR/root-install" && pwd -P)" ]; then
    echo -e "${RED}[ERROR] --root must be inside the root-install/ artifact.${NC}"
    echo "Keeping the sparse image, manifest and SHA256SUMS together prevents"
    echo "an old top-level image from being paired with this bundle."
    exit 1
fi

# deploy-bundle/SHA256SUMS covers the ESP and BOOT. root-install is a separate
# prepare-install artifact and has its own manifest/SHA256SUMS. Do not invent a
# merged checksum file that neither artifact publishes.
if [ -z "$ROOT_SHA256SUMS" ]; then
    root_dir="$ROOT_DIR"
    if [ -f "$root_dir/SHA256SUMS" ] && [ "$root_dir/SHA256SUMS" != "$WORK_DIR/SHA256SUMS" ]; then
        ROOT_SHA256SUMS="$root_dir/SHA256SUMS"
    elif [ -f "$WORK_DIR/root-install/SHA256SUMS" ]; then
        ROOT_SHA256SUMS="$WORK_DIR/root-install/SHA256SUMS"
    fi
fi

# Manifest entries are written as "./name" by the release packager and as bare
# names by prepare-install. Normalize both forms before comparing.
manifest_hash() {
    awk -v want="$1" '
        {
            name = $2
            sub(/^\*/, "", name)
            sub(/^\.\//, "", name)
            if (name == want) { print $1; exit }
        }
    ' "$2"
}

manifest_json_value() {
    awk -v key="\"$1\":" '
        $1 == key {
            value = $2
            gsub(/[",]/, "", value)
            print value
            exit
        }
    ' "$2"
}

if [ "$SKIP_CHECKSUM" -eq 0 ]; then
    if [ ! -f "$WORK_DIR/SHA256SUMS" ]; then
        echo -e "${RED}[ERROR] Missing SHA256SUMS next to the images.${NC}"
        echo "Refusing to flash unverified images. Re-download the deploy-bundle"
        echo "and keep its SHA256SUMS next to the images."
        exit 1
    fi
    if [ ! -f "$ROOT_SHA256SUMS" ]; then
        echo -e "${RED}[ERROR] Missing root-install/SHA256SUMS for the prepared root.${NC}"
        echo "Download the root-install artifact next to the deploy-bundle files,"
        echo "or pass --root-sha256sums with its SHA256SUMS."
        exit 1
    fi
    echo -e "[INFO] Verifying deploy-bundle and root-install integrity..."
    CHKSUM_TOOL=""
    if command -v sha256sum &>/dev/null; then
        CHKSUM_TOOL="sha256sum"
    elif command -v shasum &>/dev/null; then
        CHKSUM_TOOL="shasum -a 256"
    else
        echo -e "${RED}[ERROR] Neither sha256sum nor shasum is available.${NC}"
        echo "Install coreutils (Linux) or provide a SHA-256 tool on PATH."
        exit 1
    fi
    # The root-install manifest is read with python3 below. Check it here so a
    # host without python3 stops before the images are verified, not after.
    if ! command -v python3 &>/dev/null; then
        echo -e "${RED}[ERROR] 'python3' command not found; it is needed to read root-install-manifest.json.${NC}"
        echo "Install Python 3 and retry:"
        echo "  - Fedora: sudo dnf install python3"
        echo "  - Ubuntu/Debian: sudo apt install python3"
        echo "  - macOS: python3 ships with the Command Line Tools"
        exit 1
    fi

    check_manifest_image() {
        local target_file="$1" checksum_file="$2" target_name expected_hash actual_hash
        target_name=$(basename "$target_file")
        expected_hash=$(manifest_hash "$target_name" "$checksum_file")
        if [ -z "$expected_hash" ]; then
            echo -e "${RED}[ERROR] ${target_name} is not listed in ${checksum_file}.${NC}"
            echo "The image set does not match the published artifact. Re-download."
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
    }

    if [ -n "$BOOT_IMG" ] && [ "$(dirname "$BOOT_IMG")" = "$WORK_DIR" ]; then
        check_manifest_image "$BOOT_IMG" "$WORK_DIR/SHA256SUMS"
    elif [ -n "$BOOT_IMG" ] && [ -f "$(dirname "$BOOT_IMG")/SHA256SUMS" ]; then
        check_manifest_image "$BOOT_IMG" "$(dirname "$BOOT_IMG")/SHA256SUMS"
    fi
    check_manifest_image "$ESP_IMG" "$WORK_DIR/SHA256SUMS"
    check_manifest_image "$ROOT_IMG" "$ROOT_SHA256SUMS"
    root_manifest="$(dirname "$ROOT_IMG")/root-install-manifest.json"
    check_manifest_image "$root_manifest" "$ROOT_SHA256SUMS"

    for required in bundle-manifest.json root-image-manifest.json; do
        if [ ! -f "$WORK_DIR/$required" ]; then
            echo -e "${RED}[ERROR] Missing ${required}; cannot bind root-install to this bundle.${NC}"
            exit 1
        fi
    done
    bundle_manifest_sha=$($CHKSUM_TOOL "$WORK_DIR/bundle-manifest.json" | awk '{print $1}')
    root_source_bundle=$(manifest_json_value source_bundle_manifest_sha256 "$root_manifest")
    root_manifest_sha=$($CHKSUM_TOOL "$root_manifest" | awk '{print $1}')
    bundle_root_manifest_sha=$($CHKSUM_TOOL "$WORK_DIR/root-image-manifest.json" | awk '{print $1}')
    root_source_root=$(manifest_json_value source_root_manifest_sha256 "$root_manifest")
    root_transport_sha=$(manifest_json_value sha256 "$root_manifest")
    root_expanded_bytes=$(manifest_json_value expanded_bytes "$root_manifest")
    root_image_size=$(stat -c%s "$ROOT_IMG" 2>/dev/null || stat -f%z "$ROOT_IMG")
    # prepare-root-capacity now writes android_slot at the top level. Accept the
    # older nested location only for already-downloaded artifacts; both forms
    # are still bound by the root-install manifest checksum.
    root_android_slot=$(python3 -c 'import json,sys; data=json.load(open(sys.argv[1])); print(data.get("android_slot") or (data.get("device_configuration") or {}).get("android_slot") or "")' "$root_manifest")
    actual_root_sha=$($CHKSUM_TOOL "$ROOT_IMG" | awk '{print $1}')
    if [ "$root_source_bundle" != "$bundle_manifest_sha" ]; then
        echo -e "${RED}[ERROR] root-install comes from a different deploy-bundle.${NC}"
        echo "  deploy-bundle manifest: $bundle_manifest_sha"
        echo "  root-install source   : $root_source_bundle"
        exit 1
    fi
    if [ "$root_source_root" != "$bundle_root_manifest_sha" ]; then
        echo -e "${RED}[ERROR] root-install source root image manifest differs from this bundle.${NC}"
        echo "  deploy-bundle root manifest: $bundle_root_manifest_sha"
        echo "  root-install source        : $root_source_root"
        exit 1
    fi
    if [ "$root_transport_sha" != "$actual_root_sha" ]; then
        echo -e "${RED}[ERROR] root-install manifest does not describe the selected sparse image.${NC}"
        exit 1
    fi
    if [ -z "$root_expanded_bytes" ] || [ "$root_expanded_bytes" != "$ROOT_PARTITION_SIZE" ]; then
        echo -e "${RED}[ERROR] root-install was expanded to '${root_expanded_bytes}' bytes, but sunuefi_root is ${ROOT_PARTITION_SIZE} bytes.${NC}"
        echo "The root-install capacity must exactly match sunuefi_root."
        echo "Regenerate root-install from the current GPT capacity."
        exit 1
    fi
    if [ "$root_android_slot" != "_a" ]; then
        echo -e "${RED}[ERROR] root-install was prepared for Android slot '${root_android_slot}', but this installer requires '_a'.${NC}"
        echo "The first-install path keeps Android on Slot A and writes Linux to Slot B."
        echo "Generate root-install with --android-slot _a before flashing."
        exit 1
    fi
    if [ "$root_image_size" = "$ROOT_PARTITION_SIZE" ]; then
        echo -e "${RED}[ERROR] root-install is a raw image, not the sparse transport from prepare-install.${NC}"
        exit 1
    fi
    echo -e "  ${GREEN}✓${NC} root-install provenance matches this deploy-bundle."
else
    echo -e "${RED}[ERROR] --skip-checksum is not accepted for the first-install path.${NC}"
    echo "The installer must verify both SHA256SUMS, the bundle/root provenance,"
    echo "the Android slot, and the exact sunuefi_root capacity before any write."
    exit 1
fi

echo -e "[INFO] Flashing targets resolved:"
if [ -n "$BOOT_IMG" ]; then
    echo -e "  - Boot image   : ${GREEN}${BOOT_IMG}${NC} ($(stat -c%s "$BOOT_IMG" 2>/dev/null || stat -f%z "$BOOT_IMG") bytes)"
else
    echo -e "  - Boot image   : ${YELLOW}none${NC}; boot_b is left untouched"
fi
echo -e "  - ESP image    : ${GREEN}${ESP_IMG}${NC} ($(stat -c%s "$ESP_IMG" 2>/dev/null || stat -f%z "$ESP_IMG") bytes)"
echo -e "  - RootFS image : ${GREEN}${ROOT_IMG}${NC} (${root_image_size} transfer bytes, ${root_expanded_bytes} expanded bytes)"
if [ -n "$SSH_KEY" ]; then
    echo -e "  - SSH key      : ${GREEN}${SSH_KEY}${NC} (injected into the ESP before flashing)"
fi

# 7. Safety Confirmation
if [ "$AUTO_CONFIRM" -eq 0 ]; then
    echo -e "\n${YELLOW}${BOLD}⚠️  CRITICAL SAFETY WARNING ⚠️${NC}"
    echo "1. This action will write Linux SunUEFI to Slot B (boot_b only)."
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

# 7. Optional install-time SSH key.
# The image ships no authorized_keys. A key passed here is written into the ESP
# image only after both SHA256SUMS, the bundle/root provenance, the slot and the
# capacity have been verified, and before sunuefi_esp is flashed. The device's
# piano-first-boot-ssh-key.service imports it on first boot and deletes it.
ESP_SHA256=""
sha256_of() {
    if [ "$CHKSUM_TOOL" = "sha256sum" ]; then
        sha256sum "$1" | awk '{print $1}'
    else
        shasum -a 256 "$1" | awk '{print $1}'
    fi
}

inject_ssh_key() {
    local key_file="$1" line type blob
    if [ ! -f "$key_file" ] || [ -L "$key_file" ]; then
        echo -e "${RED}[ERROR] --ssh-key must name a regular public-key file: ${key_file}${NC}"
        exit 1
    fi
    if [ "$(wc -l < "$key_file")" -ne 1 ]; then
        echo -e "${RED}[ERROR] --ssh-key must contain exactly one public key.${NC}"
        echo "Pass the .pub file, never a private key."
        exit 1
    fi
    line=$(tr -d '\r' < "$key_file" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')
    type=$(printf '%s\n' "$line" | awk '{print $1}')
    blob=$(printf '%s\n' "$line" | awk '{print $2}')
    case "$type" in
        ssh-ed25519|ssh-rsa) ;;
        *)
            echo -e "${RED}[ERROR] --ssh-key has unsupported type '${type:-}'.${NC}"
            echo "Only ssh-ed25519 and ssh-rsa public keys are accepted."
            exit 1
            ;;
    esac
    if [ -z "$blob" ] || ! printf '%s' "$blob" | base64 -d >/dev/null 2>&1; then
        echo -e "${RED}[ERROR] --ssh-key has invalid base64 key material.${NC}"
        exit 1
    fi
    if ! command -v mcopy &>/dev/null; then
        echo -e "${RED}[ERROR] 'mcopy' command not found; it is needed to inject the SSH key.${NC}"
        echo "Install mtools and retry:"
        echo "  - Fedora: sudo dnf install mtools"
        echo "  - Ubuntu/Debian: sudo apt install mtools"
        echo "  - macOS: brew install mtools"
        exit 1
    fi
    mcopy -o -i "$ESP_IMG" "$key_file" ::/piano-ssh-key.pub
    ESP_SHA256=$(sha256_of "$ESP_IMG")
    echo -e "  ${GREEN}✓${NC} SSH public key written to ${ESP_IMG} as /piano-ssh-key.pub"
    echo -e "    ${YELLOW}The ESP image digest changed; compare a readback against:${NC}"
    echo "    $ESP_SHA256"
}

if [ -n "$SSH_KEY" ]; then
    echo -e "\n[INFO] Injecting the install-time SSH public key..."
    inject_ssh_key "$SSH_KEY"
else
    echo -e "\n[INFO] No --ssh-key given; the system keeps password login only."
fi

# 7. Flashing execution sequence
echo -e "\n${BLUE}${BOLD}=== Starting Flashing Sequence ===${NC}"

if [ -n "$BOOT_IMG" ]; then
    python3 - "$BOOT_IMG" <<'PYEOF'
import struct, sys
from pathlib import Path
path = Path(sys.argv[1])
data = path.read_bytes()
if len(data) != 100663296:
    sys.exit(f"{path.name}: {len(data)} bytes, expected the 96 MiB boot_b image")
if data[:8] != b"ANDROID!":
    sys.exit(f"{path.name}: not an Android boot container")
version = struct.unpack_from("<I", data, 40)[0]
if version != 4:
    sys.exit(f"{path.name}: Header v{version}; SM8750 ABL requires v4")
if struct.unpack_from("<I", data, 16)[0] == 0:
    sys.exit(f"{path.name}: os_version is 0; SM8750 ABL rejects it")
if b"AVB0" not in data:
    sys.exit(f"{path.name}: no AVB footer")
if b"SUNUEFI-NEXTv1\0" not in data:
    sys.exit(f"{path.name}: no SUNUEFI-NEXTv1 boot request page")
PYEOF
fi

if [ -n "$BOOT_IMG" ]; then
    echo -e "\n--> Step 1/4: Setting active boot slot to B..."
    fb set_active b
else
    echo -e "\n--> Step 1/3: No boot_b image given; the boot slot is left as it is."
    echo -e "    ${YELLOW}boot_b keeps its current content. Run prepare-device-boot.py and pass${NC}"
    echo -e "    ${YELLOW}--boot device-boot/boot_linux.img to write it, then set the slot yourself.${NC}"
fi

# dtbo_b is deliberately left untouched. Qualcomm ABL authenticates the whole
# boot chain of the active slot and rejects an empty/foreign DTBO with
# "Failed to load/authenticate boot image: Device Error"; erasing dtbo_b has
# been observed to leave the tablet stuck in Fastboot. SunUEFI reads its board
# DTB from vendor_boot_b plus the stock dtbo_b overlay fragments, so the stock
# content must stay in place.
echo -e "\n--> Skipping dtbo_b: keeping the stock DTBO for the active slot."

if [ -n "$BOOT_IMG" ]; then
    echo -e "\n--> Step 2/4: Flashing device boot image to boot_b..."
    fb flash boot_b "$BOOT_IMG"
    echo -e "\n--> Step 3/4: Flashing SunUEFI ESP to sunuefi_esp..."
else
    echo -e "\n--> Step 2/3: Flashing SunUEFI ESP to sunuefi_esp..."
fi
fb flash sunuefi_esp "$ESP_IMG"

if [ -n "$BOOT_IMG" ]; then
    echo -e "\n--> Step 4/4: Flashing Fedora 44 RootFS to sunuefi_root..."
else
    echo -e "\n--> Step 3/3: Flashing Fedora 44 RootFS to sunuefi_root..."
fi
fb flash sunuefi_root "$ROOT_IMG"

echo -e "\n${GREEN}${BOLD}====================================================================${NC}"
echo -e "${GREEN}${BOLD}             🎉 Flashing Completed Successfully! 🎉                 ${NC}"
echo -e "${GREEN}${BOLD}====================================================================${NC}"
echo ""
echo "System Summary:"
echo "  - Default desktop user : piano"
echo "  - Default password     : piano123456"
if [ -n "$ESP_SHA256" ]; then
    echo "  - SSH key              : imported on first boot; password login still works"
fi
echo "  - SunUEFI boot menu    : Press Volume keys during initial 3-second logo"
echo "  - Switch back to Android: Open GNOME App Drawer -> Click 'Return to Android'"
echo ""

if [ "$DO_REBOOT" -eq 1 ] && [ -z "$BOOT_IMG" ]; then
    echo -e "--> Not rebooting: boot_b was not written, so the active slot stays as it was."
elif [ "$DO_REBOOT" -eq 1 ]; then
    echo -e "--> Rebooting tablet into Fedora 44 Linux..."
    fb reboot
    echo -e "${GREEN}Device reboot triggered. Enjoy Fedora Linux on Xiaomi Pad 8 Pro!${NC}"
else
    echo -e "Device left in Fastboot mode (--no-reboot specified)."
fi

#!/usr/bin/env bash
# ==============================================================================
# package-release-bundle.sh - Assemble and verify GitHub Release bundle for piano
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
INSTALLER_SRC="$SCRIPT_DIR/installer"
DEFAULT_OUT="$REPO_ROOT/out/release-bundle"
BUILD_TMP="${TMPDIR:-$REPO_ROOT/../tmp/trash}/release-pack-work"

OUT_DIR="$DEFAULT_OUT"
RELEASE_VER="$(date +%Y%m%d)"
BOOT_IN=""
ESP_IN=""
ROOT_IN=""
KSU_IN=""
CHECK_ONLY=0

usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Assemble, compress, and verify delivery assets for GitHub Releases.

Options:
  --out-dir <dir>       Target output directory (default: out/release-bundle)
  --version <tag>       Release version tag (default: current date)
  --boot-img <file>     Path to boot_sunuefi.img
  --esp-img <file>      Path to sunuefi_esp.img
  --root-img <file>     Path to pianoroot-fedora44-gnome-f2fs.img or .img.zst
  --ksu-zip <file>      Path to piano-switch-ksu / SunUEFI Android module zip
  --check-only          Only verify existing bundle in out-dir
  -h, --help            Show this help message and exit

EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --out-dir)
            OUT_DIR="$2"
            shift 2
            ;;
        --version)
            RELEASE_VER="$2"
            shift 2
            ;;
        --boot-img)
            BOOT_IN="$2"
            shift 2
            ;;
        --esp-img)
            ESP_IN="$2"
            shift 2
            ;;
        --root-img)
            ROOT_IN="$2"
            shift 2
            ;;
        --ksu-zip)
            KSU_IN="$2"
            shift 2
            ;;
        --check-only)
            CHECK_ONLY=1
            shift
            ;;
        -h|--help)
            usage
            ;;
        *)
            echo "Error: Unknown argument '$1'" >&2
            exit 1
            ;;
    esac
done

echo "=== Piano GitHub Release Packager ==="
echo "Release Version : $RELEASE_VER"
echo "Output Dir      : $OUT_DIR"
echo ""

mkdir -p "$OUT_DIR"
mkdir -p "$BUILD_TMP"

if [ "$CHECK_ONLY" -eq 1 ]; then
    echo "--> Running integrity check on existing bundle in $OUT_DIR..."
    if [ ! -f "$OUT_DIR/SHA256SUMS" ]; then
        echo "Error: $OUT_DIR/SHA256SUMS not found!" >&2
        exit 1
    fi
    (cd "$OUT_DIR" && sha256sum -c SHA256SUMS)
    echo "Integrity verification passed."
    exit 0
fi

# 1. Package piano-installer.zip
echo "--> Packaging piano-installer.zip..."
INSTALLER_ZIP="$OUT_DIR/piano-installer-${RELEASE_VER}.zip"
(
    cd "$INSTALLER_SRC"
    zip -q -r -9 "$INSTALLER_ZIP" flash-linux.sh flash-linux.bat README-INSTALL.txt
)
echo "    Created: $(basename "$INSTALLER_ZIP") ($(stat -c%s "$INSTALLER_ZIP" 2>/dev/null || stat -f%z "$INSTALLER_ZIP") bytes)"

# Helper function to resolve candidate files
resolve_candidate() {
    local explicit="$1"
    shift
    if [ -n "$explicit" ] && [ -f "$explicit" ]; then
        echo "$explicit"
        return 0
    fi
    for cand in "$@"; do
        if [ -f "$cand" ]; then
            echo "$cand"
            return 0
        fi
    done
    return 1
}

# 2. Resolve and copy Boot image
BOOT_FOUND=$(resolve_candidate "$BOOT_IN" \
    "$REPO_ROOT/out/device-boot-a485-20261015/boot_linux.img" \
    "$REPO_ROOT/out/artifacts/uefi-a485-38560284113/extracted/PianoUEFI-product.img" \
    "$REPO_ROOT/../mipad8p-piano/out/offline-bringup-20261011-r2/deploy-bundle/boot.img" || true)

if [ -n "$BOOT_FOUND" ]; then
    echo "--> Copying boot image: $BOOT_FOUND"
    cp -v "$BOOT_FOUND" "$OUT_DIR/boot_sunuefi.img"
else
    echo "--> NOTE: No boot image found in default search paths. Supply --boot-img if available."
fi

# 3. Resolve and copy ESP image
ESP_FOUND=$(resolve_candidate "$ESP_IN" \
    "$REPO_ROOT/out/device-backup/readonly-20261014/current-sunuefi_esp.img" \
    "$REPO_ROOT/../mipad8p-piano/out/offline-bringup-20261011-r2/deploy-bundle/esp.img" || true)

if [ -n "$ESP_FOUND" ]; then
    echo "--> Copying ESP image: $ESP_FOUND"
    cp -v "$ESP_FOUND" "$OUT_DIR/sunuefi_esp.img"
else
    echo "--> NOTE: No ESP image found in default search paths. Supply --esp-img if available."
fi

# 4. Resolve and copy/compress RootFS image
ROOT_FOUND=$(resolve_candidate "$ROOT_IN" \
    "$REPO_ROOT/../mipad8p-piano/out/offline-bringup-20261011-r2/deploy-bundle/pianoroot.f2fs.img.zst" \
    "$REPO_ROOT/out/deploy-stage-20261014/pianoroot.f2fs.img.zst" || true)

if [ -n "$ROOT_FOUND" ]; then
    if [[ "$ROOT_FOUND" == *.zst ]]; then
        echo "--> Copying compressed RootFS: $ROOT_FOUND"
        cp -v "$ROOT_FOUND" "$OUT_DIR/pianoroot-fedora44-gnome-f2fs.img.zst"
    else
        echo "--> Compressing RootFS image with zstd: $ROOT_FOUND ..."
        zstd -T0 -19 -f "$ROOT_FOUND" -o "$OUT_DIR/pianoroot-fedora44-gnome-f2fs.img.zst"
    fi
else
    echo "--> NOTE: No RootFS image found in default search paths. Supply --root-img if available."
fi

# 5. Resolve and copy Android dual-boot KSU module
KSU_FOUND=$(resolve_candidate "$KSU_IN" \
    "$REPO_ROOT/out/android-matching-core-20261015/piano-matching-core-20261015.zip" || true)

if [ -n "$KSU_FOUND" ]; then
    echo "--> Copying Android dual-boot module: $KSU_FOUND"
    cp -v "$KSU_FOUND" "$OUT_DIR/piano-switch-ksu-${RELEASE_VER}.zip"
else
    echo "--> NOTE: No Android KSU module zip found. Supply --ksu-zip if available."
fi

# 6. Generate SHA256SUMS
echo ""
echo "--> Generating SHA256 checksums in $OUT_DIR/SHA256SUMS..."
(
    cd "$OUT_DIR"
    find . -maxdepth 1 -type f ! -name "SHA256SUMS" | sort | while read -r item; do
        sha256sum "$item"
    done > SHA256SUMS
)

echo ""
echo "--> Verifying checksum integrity..."
(
    cd "$OUT_DIR"
    sha256sum -c SHA256SUMS
)

echo ""
echo "=== Release Bundle Ready ==="
ls -lh "$OUT_DIR"
echo ""
echo "Successfully packaged release bundle."

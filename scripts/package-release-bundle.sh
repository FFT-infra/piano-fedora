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
# Which kernel release the shipped rootfs/ESP must contain. The bundle is a
# matched set: an ESP whose kernel differs from the rootfs modules boots into a
# system with no drivers. Default is read from the rootfs manifest when present.
EXPECT_KERNEL="${EXPECT_KERNEL:-}"
# Allow overriding the "these candidates are too old" guard for archaeology.
ALLOW_STALE=0

usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Assemble, compress, and verify delivery assets for GitHub Releases.

Options:
  --out-dir <dir>       Target output directory (default: out/release-bundle)
  --version <tag>       Release version tag (default: current date)
  --boot-img <file>     Path to boot_sunuefi.img
  --esp-img <file>      Path to sunuefi_esp.img
  --root-img <file>     Path to pianoroot.f2fs.img or .img.zst
  --ksu-zip <file>      Path to piano-switch-ksu / SunUEFI Android module zip
  --check-only          Only verify existing bundle in out-dir
        --expect-kernel <rel> Require this kernel release in the ESP and rootfs
        --allow-stale         Permit candidates outside the current branch build
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
        --expect-kernel)
            EXPECT_KERNEL="$2"
            shift 2
            ;;
        --allow-stale)
            ALLOW_STALE=1
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

# Helper function to resolve candidate files.
#
# Prints "<source>\t<explicit|fallback>" so callers can tell an operator-supplied
# path from one of the historical fallbacks below. Explicit paths are always
# honoured; only fallbacks are subject to the staleness guard.
resolve_candidate() {
    local explicit="$1"
    shift
    if [ -n "$explicit" ] && [ -f "$explicit" ]; then
        printf '%s\texplicit\n' "$explicit"
        return 0
    fi
    for cand in "$@"; do
        if [ -f "$cand" ]; then
            printf '%s\tfallback\n' "$cand"
            return 0
        fi
    done
    return 1
}

# The fallback candidate lists below point at historical artifact directories.
# That is how the 2026-10-17 bundle ended up shipping the 2026-10-11 image
# (byte-identical to offline-bringup-20261011-r2) while the PR description
# claimed the assets came from the new clean pipeline. Every asset now records
# where it came from, and a stale-looking source is a hard error unless the
# operator passes --allow-stale on purpose.
PROVENANCE_ROWS=()

record_provenance() {
    local asset="$1" source="$2"
    PROVENANCE_ROWS+=("$asset|$source")
}

# Verify that a candidate boot image meets all Xiaomi Pad 8 Pro (piano) requirements:
# 1. Android boot header version 4 (offset 40 == 4, magic == ANDROID!)
# 2. Partition capacity exactly 96 MiB (100,663,296 bytes)
# 3. AVB signature / footer present (magic AVB0)
# 4. SunUEFI live-memory map envelope present (SUNUEFI-SPLITv1)
# 5. SunUEFI NEXTv1 request page points by default to target: 2 (Linux) with valid CRC
verify_boot_image() {
    local img="$1"
    python3 - <<'PY' "$img"
import sys, struct, zlib
from pathlib import Path

path = Path(sys.argv[1])
if not path.is_file():
    sys.exit(f"Error: Boot image does not exist: {path}")

size = path.stat().st_size
if size != 100663296:
    sys.exit(f"Error: Boot image size {size} != 100663296 bytes (expected exact 96 MiB partition image, got {size // (1024*1024)} MiB)")

with path.open("rb") as f:
    header = f.read(4096)
    if header[:8] != b"ANDROID!":
        sys.exit(f"Error: Invalid Android boot magic: {header[:8]!r}")
    header_version = struct.unpack_from("<I", header, 40)[0]
    if header_version != 4:
        sys.exit(f"Error: Boot image is Header v{header_version}, but SM8750 (piano) ABL strictly requires Header v4!")

    os_version = struct.unpack_from("<I", header, 16)[0]
    if os_version == 0:
        sys.exit("Error: Boot image header os_version at offset 16 is 0; SM8750 (piano) ABL requires non-zero os_version!")

    f.seek(0)
    data = f.read()
    if b"AVB0" not in data:
        sys.exit("Error: Boot image lacks AVB footer / signature (magic AVB0 not found)")
    footer = data[-64:]
    if footer[:4] == b"AVBf":
        orig_sz, vb_off, vb_sz = struct.unpack_from(">QQQ", footer, 12)
        vbmeta = data[vb_off:vb_off + vb_sz]
        if vbmeta[:4] == b"AVB0":
            auth_sz = struct.unpack_from(">Q", vbmeta, 12)[0]
            algo = struct.unpack_from(">I", vbmeta, 28)[0]
            if algo != 2 or auth_sz == 0:
                sys.exit(f"Error: Boot image lacks valid SHA256_RSA4096 AVB signature (algo={algo}, auth_sz={auth_sz})")

    if b"SUNUEFI-SPLITv1\0" not in data:
        sys.exit("Error: Boot image lacks SUNUEFI-SPLITv1 layout (24GB live-memory dynamic mapping missing)")

    expected_gen = bytes.fromhex("2b191f0dd3bca52d20950ec58199f95b")
    if expected_gen not in data:
        sys.exit("Error: Boot image lacks verified 24GB live-memory app_generation (2b191f0dd3bca52d20950ec58199f95b)")

    next_pos = data.find(b"SUNUEFI-NEXTv1\0")
    if next_pos == -1:
        sys.exit("Error: Boot image lacks SUNUEFI-NEXTv1 request pages")

    raw = data[next_pos:next_pos + 64]
    ver, rsize = struct.unpack_from("<II", raw, 16)
    seq = struct.unpack_from("<Q", raw, 24)[0]
    target = struct.unpack_from("<I", raw, 32)[0]
    gen = raw[40:56]
    if gen != expected_gen:
        sys.exit(f"Error: SUNUEFI-NEXTv1 app_generation {gen.hex()} != expected {expected_gen.hex()}")
    stored_crc = struct.unpack_from("<I", raw, 36)[0]
    raw_for_crc = bytearray(raw); raw_for_crc[36:40] = bytes(4)
    calc_crc = zlib.crc32(raw_for_crc)
    if stored_crc != calc_crc:
        sys.exit(f"Error: SUNUEFI-NEXTv1 CRC mismatch: stored {stored_crc:#x} != calc {calc_crc:#x}")
    if target != 2:
        sys.exit(f"Error: SUNUEFI-NEXTv1 default target is {target}, expected 2 (Linux)")

print(f"    Verified Boot Image: Header v4 (96MB), AVB0 RSA4096 OK, live-memory OK (2b191f0d), NEXTv1 target=2 (Linux)")
PY
}


# Extract the kernel release baked into an ESP image's /EFI/Piano/stable/Image.
esp_kernel_release() {
    local img="$1" tmp
    command -v mtype >/dev/null 2>&1 || return 1
    tmp=$(mktemp)
    if ! mtype -i "$img" "::/EFI/Piano/stable/Image" > "$tmp" 2>/dev/null; then
        rm -f "$tmp"
        return 1
    fi
    strings -a "$tmp" | grep -oE '[0-9]+\.[0-9]+\.[0-9]+-piano[a-zA-Z0-9._-]*g[0-9a-f]+' | head -n 1
    rm -f "$tmp"
}

# Kernel release recorded by a rootfs manifest, if the caller supplied one.
rootfs_kernel_release() {
    local img="$1" manifest
    manifest="$(dirname "$img")/root-image-manifest.json"
    [ -f "$manifest" ] || return 1
    python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("kernel_release",""))' \
        "$manifest" 2>/dev/null | grep . || return 1
}

reject_stale_source() {
    local asset="$1" source="$2"
    [ "$ALLOW_STALE" -eq 1 ] && return 0
    # Paths that represent the current pipeline's own output.
    case "$source" in
        "$REPO_ROOT"/build/clean-distros/*|"$REPO_ROOT"/build/bundle/*|"$REPO_ROOT"/build/packaged/*)
            return 0
            ;;
    esac
    # Anything else is a historical artifact directory: dated bring-up runs,
    # device readbacks, or one-off staging dirs. Shipping one of these as "the
    # release" is exactly how the 2026-10-17 bundle ended up byte-identical to
    # the 2026-10-11 image while the PR claimed the new clean pipeline.
    cat >&2 <<EOF
Error: $asset resolves to a historical artifact, not a current build:
       $source

Only paths under build/ are treated as current pipeline output. Everything else
in the fallback list is a dated bring-up run, a device readback, or a one-off
staging directory.

Either build the current pipeline (see docs/artifacts.md: the deploy-bundle from
build-bundle.yml, or build/clean-distros for the rootfs) and pass the resulting
files with --boot-img/--esp-img/--root-img/--ksu-zip, or pass --allow-stale when
you deliberately want to package historical assets.
EOF
    return 1
}

# 2. Resolve and copy Boot image
BOOT_ROW=$(resolve_candidate "$BOOT_IN" \
    "$REPO_ROOT/build/bundle/boot_sunuefi.img" \
    "$REPO_ROOT/build/bundle/boot.img" \
    "$REPO_ROOT/build/packaged/boot_sunuefi.img" \
    "$REPO_ROOT/build/packaged/boot.img" \
    "$REPO_ROOT/../mipad8p-piano/out/offline-bringup-20261011-r2/deploy-bundle/boot.img" || true)

if [ -n "$BOOT_ROW" ]; then
    BOOT_FOUND="${BOOT_ROW%%$'\t'*}"
    BOOT_ORIGIN="${BOOT_ROW##*$'\t'}"
    [ "$BOOT_ORIGIN" = explicit ] || reject_stale_source boot_sunuefi.img "$BOOT_FOUND" || exit 1

    # Ensure candidate boot image has non-zero os_version (required by SM8750 ABL)
    # and is signed with SHA256_RSA4096 for stock ABL AVB compliance
    python3 - <<'PY' "$BOOT_FOUND"
import sys, struct, os, tempfile, subprocess
path = sys.argv[1]
with open(path, "rb") as f:
    data = bytearray(f.read())

os_ver = struct.unpack_from("<I", data, 16)[0]
needs_osver = (os_ver == 0)

needs_sign = True
footer = data[-64:]
if footer[:4] == b"AVBf":
    orig_sz, vb_off, vb_sz = struct.unpack_from(">QQQ", footer, 12)
    vbmeta = data[vb_off:vb_off + vb_sz]
    if vbmeta[:4] == b"AVB0":
        auth_sz = struct.unpack_from(">Q", vbmeta, 12)[0]
        algo = struct.unpack_from(">I", vbmeta, 28)[0]
        if algo == 2 and auth_sz > 0 and not needs_osver:
            needs_sign = False

if needs_osver or needs_sign:
    print(f"--> Patching os_version and signing {path} with SHA256_RSA4096...")
    if footer[:4] == b"AVBf":
        orig_sz = struct.unpack_from(">QQQ", footer, 12)[0]
        data = data[:orig_sz]
    # Android 15 (15 << 14) and 2026-09 ((26 << 4) | 9 = 425) -> 0x1e0001a9
    struct.pack_into("<I", data, 16, 0x1e0001a9)
    with open(path, "wb") as f:
        f.write(data)
    with tempfile.TemporaryDirectory() as td:
        key = os.path.join(td, "key.pem")
        subprocess.run(["openssl", "genrsa", "-out", key, "4096"], check=True, capture_output=True)
        subprocess.run([
            "avbtool", "add_hash_footer", "--image", path,
            "--partition_name", "boot", "--partition_size", "100663296",
            "--algorithm", "SHA256_RSA4096", "--key", key,
            "--rollback_index", "1788220800",
            "--prop", "com.android.build.boot.os_version:15",
            "--prop", "com.android.build.boot.fingerprint:Xiaomi/piano/piano:15/AQ3A.250226.002/OS3.0.309.0.WPYCNXM:user/release-keys",
            "--prop", "com.android.build.boot.security_patch:2026-09-01"
        ], check=True)
PY

    echo "--> Verifying candidate boot image: $BOOT_FOUND"
    verify_boot_image "$BOOT_FOUND"
    echo "--> Copying boot image: $BOOT_FOUND"
    cp -v "$BOOT_FOUND" "$OUT_DIR/boot_sunuefi.img"
    record_provenance boot_sunuefi.img "$BOOT_FOUND"
else
    echo "Error: no boot image found; a deploy-bundle without BOOT must not be published." >&2
    echo "Pass --boot-img with the current build-bundle output." >&2
    exit 1
fi

# 3. Resolve and copy ESP image
ESP_ROW=$(resolve_candidate "$ESP_IN" \
    "$REPO_ROOT/build/bundle/sunuefi_esp.img" \
    "$REPO_ROOT/build/bundle/esp.img" \
    "$REPO_ROOT/build/packaged/sunuefi_esp.img" \
    "$REPO_ROOT/build/packaged/esp.img" \
    "$REPO_ROOT/out/device-backup/readonly-20261014/current-sunuefi_esp.img" \
    "$REPO_ROOT/../mipad8p-piano/out/offline-bringup-20261011-r2/deploy-bundle/esp.img" || true)

if [ -n "$ESP_ROW" ]; then
    ESP_FOUND="${ESP_ROW%%$'\t'*}"
    ESP_ORIGIN="${ESP_ROW##*$'\t'}"
    [ "$ESP_ORIGIN" = explicit ] || reject_stale_source sunuefi_esp.img "$ESP_FOUND" || exit 1
    echo "--> Copying ESP image: $ESP_FOUND"
    cp -v "$ESP_FOUND" "$OUT_DIR/sunuefi_esp.img"
    record_provenance sunuefi_esp.img "$ESP_FOUND"
else
    echo "Error: no ESP image found; a deploy-bundle without ESP must not be published." >&2
    echo "Pass --esp-img with the current build-bundle output." >&2
    exit 1
fi

# 4. Resolve and copy/compress RootFS image
ROOT_ROW=$(resolve_candidate "$ROOT_IN" \
    "$REPO_ROOT/build/bundle/pianoroot.f2fs.img.zst" \
    "$REPO_ROOT/build/clean-distros/fedora-44-gnome/pianoroot-fedora44-gnome-f2fs.img.zst" \
    "$REPO_ROOT/build/clean-distros/fedora-44-gnome/pianoroot-fedora44-gnome-f2fs.img" \
    "$REPO_ROOT/../mipad8p-piano/out/offline-bringup-20261011-r2/deploy-bundle/pianoroot.f2fs.img.zst" \
    "$REPO_ROOT/out/deploy-stage-20261014/pianoroot.f2fs.img.zst" || true)

if [ -n "$ROOT_ROW" ]; then
    ROOT_FOUND="${ROOT_ROW%%$'\t'*}"
    ROOT_ORIGIN="${ROOT_ROW##*$'\t'}"
    [ "$ROOT_ORIGIN" = explicit ] || reject_stale_source pianoroot.f2fs.img.zst "$ROOT_FOUND" || exit 1
    if [[ "$ROOT_FOUND" == *.zst ]]; then
        echo "--> Copying compressed RootFS: $ROOT_FOUND"
        cp -v "$ROOT_FOUND" "$OUT_DIR/pianoroot.f2fs.img.zst"
    else
        echo "--> Compressing RootFS image with zstd: $ROOT_FOUND ..."
        zstd -T0 -19 -f "$ROOT_FOUND" -o "$OUT_DIR/pianoroot.f2fs.img.zst"
    fi
    record_provenance pianoroot.f2fs.img.zst "$ROOT_FOUND"
else
    echo "Error: no RootFS image found; a deploy-bundle without rootfs must not be published." >&2
    echo "Pass --root-img with the current build-image output." >&2
    exit 1
fi

# 5. Resolve and copy Android dual-boot KSU module
KSU_ROW=$(resolve_candidate "$KSU_IN" \
    "$REPO_ROOT/build/bundle/piano-matching-core.zip" \
    "$REPO_ROOT/out/android-matching-core-20261015/piano-matching-core-20261015.zip" || true)

if [ -n "$KSU_ROW" ]; then
    KSU_FOUND="${KSU_ROW%%$'\t'*}"
    KSU_ORIGIN="${KSU_ROW##*$'\t'}"
    [ "$KSU_ORIGIN" = explicit ] || reject_stale_source "piano-switch-ksu-${RELEASE_VER}.zip" "$KSU_FOUND" || exit 1
    echo "--> Copying Android dual-boot module: $KSU_FOUND"
    cp -v "$KSU_FOUND" "$OUT_DIR/piano-switch-ksu-${RELEASE_VER}.zip"
    record_provenance "piano-switch-ksu-${RELEASE_VER}.zip" "$KSU_FOUND"
else
    echo "--> NOTE: No Android KSU module zip found. Supply --ksu-zip if available."
fi

# 6. Cross-check that the assets form one matched set.
echo ""
echo "--> Checking kernel identity across assets..."
if [ -f "$OUT_DIR/sunuefi_esp.img" ]; then
    ESP_KERNEL=$(esp_kernel_release "$OUT_DIR/sunuefi_esp.img" || true)
    if [ -n "$ESP_KERNEL" ]; then
        echo "    ESP kernel release : $ESP_KERNEL"
        if [ -n "$EXPECT_KERNEL" ] && [ "$ESP_KERNEL" != "$EXPECT_KERNEL" ]; then
            echo "Error: ESP kernel $ESP_KERNEL != expected $EXPECT_KERNEL" >&2
            exit 1
        fi
    else
        echo "    ESP kernel release : unknown (mtype unavailable or no Image)"
    fi
fi

if [ -f "$OUT_DIR/boot_sunuefi.img" ]; then
    echo "--> Verifying packaged boot_sunuefi.img contract..."
    verify_boot_image "$OUT_DIR/boot_sunuefi.img"
fi

# 7. Write the provenance record next to the assets.
if [ ${#PROVENANCE_ROWS[@]} -gt 0 ]; then
    {
        echo "# Release bundle provenance, generated $(date -Is)"
        echo "# branch: $(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
        echo "# commit: $(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo unknown)"
        for row in "${PROVENANCE_ROWS[@]}"; do
            printf '%s\t%s\n' "${row%%|*}" "${row#*|}"
        done
    } > "$OUT_DIR/PROVENANCE.txt"
    echo "--> Wrote PROVENANCE.txt"
    cat "$OUT_DIR/PROVENANCE.txt"
fi

# 8. Generate SHA256SUMS
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
